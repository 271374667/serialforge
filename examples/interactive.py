"""实时交互：用 ``while`` + ``input()`` 手动和虚拟设备对话（无硬件）。

输入命令后回车，示例会把请求发给设备并把回包打印出来：

===============  ==========================================================
命令             说明
===============  ==========================================================
``Version``      单发单收
``ReadAll``      单发多收（设备连回三帧）
``monitor``      让设备开始流式吐帧，输入 ``stop`` 结束
``alarm``        让设备主动上报一条报警
``raw Ping``     发送未注册的原始文本；设备的回包会作为未识别帧上报
``help``         打印这张表
``quit``         断开并退出
===============  ==========================================================

虚拟设备与它完整指令集见 ``examples/virtual_device.py``。
运行：``uv run python examples/interactive.py``

``connect()`` 是阻塞的，返回后就能发命令；``input()`` 反而会阻塞 Qt 事件循环，
所以示例在每次输入后手动泵一小段时间，让回包有机会送达。真实 GUI 程序不要让
``input()`` 占住事件线程，应该把收发做成信号槽。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.virtual_device import (
    ALARM,
    MONITOR,
    STOP,
    VERSION,
    Alarm,
    VirtualDevice,
)
from serialforge import (
    DeviceProfile,
    Message,
    MessageCategory,
    SerialForge,
    SerialForgeError,
)
from serialforge.advanced import CommandPriority, CommandTicket, SerialConfig
from serialforge.qt_core import QCoreApplication


class InteractiveConsole:
    """把设备当成一个可以用命令行对话的对象。"""

    PROMPT = "serialforge> "
    # 每次输入之后泵事件的时间：太短看不到流式帧，太长会让手感变差。
    PUMP_S = 0.3
    HELP = (
        "  Version    单发单收\n"
        "  ReadAll    单发多收（三帧）\n"
        "  monitor    开始流式输出；流式期间普通命令会得到 BUSY，先 stop\n"
        "  stop       停止流式输出\n"
        "  alarm      让设备主动上报一条报警\n"
        "  raw Ping   发送未注册的原始文本（回包按未识别帧上报）\n"
        "  help       显示本帮助\n"
        "  quit       断开并退出"
    )

    def __init__(self) -> None:
        """准备 Qt 应用、虚拟设备、handler 与信号槽。"""
        self._application = QCoreApplication.instance() or QCoreApplication([])
        self._device = VirtualDevice()
        self._handler = SerialForge(self._build_profile())
        # 正在跑的流式命令：需要留着 ticket 才能在 stop 时取消它。
        self._monitor_ticket: CommandTicket | None = None
        for declaration in self._device.declarations():
            self._handler.add(declaration)
        # 接收全部放在槽里：结果、事件（含未识别帧）都从信号来。
        self._handler.received.connect(
            lambda message: (
                self._on_result(message)
                if message.category is MessageCategory.COMMAND_RESULT
                else None
            )
        )
        self._handler.received.connect(
            lambda message: (
                self._on_event(message)
                if message.category
                in (
                    MessageCategory.DEVICE_EVENT,
                    MessageCategory.STREAM_FRAME,
                    MessageCategory.UNKNOWN_FRAME,
                )
                else None
            )
        )

    def _build_profile(self) -> DeviceProfile:
        """设备描述：身份与波特率来自虚拟设备，probe 是版本查询。"""
        return DeviceProfile(
            vid_pid=list(self._device.vid_pid),
            baudrates=[self._device.baud_rate],
            probe=VERSION,
            serial=SerialConfig(settle_time_s=0),
        )

    def run(self) -> int:
        """连上设备，然后进入读取输入的主循环。"""
        with self._device.use():
            try:
                # 阻塞式连接：返回时设备已就绪；失败同步抛 SerialForgeError。
                self._handler.connect()
                print(f"已连接虚拟设备（{self._handler.state.value}）")
                print("可用命令：")
                print(self.HELP)
                while self._read_once():
                    pass
            except SerialForgeError as error:
                print(f"连接失败: {error}")
                return 1
            finally:
                # 无论正常退出还是 EOF/Ctrl+C，都要断开，避免 I/O 线程悬挂。
                self._handler.disconnect()
        print("已断开连接")
        return 0

    def _read_once(self) -> bool:
        """读一行输入并处理；返回 False 表示该退出循环。"""
        try:
            text = input(self.PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            # 管道输入结束或 Ctrl+C：当成退出，不再打印提示符。
            print()
            return False
        if text in {"quit", "exit"}:
            return False
        self._dispatch(text)
        self._pump(self.PUMP_S)
        return True

    def _dispatch(self, text: str) -> None:
        """把一行输入翻译成库调用。"""
        if not text:
            return
        if text == "help":
            print(self.HELP)
        elif text == "monitor":
            # 流式命令不会自己结束，需要 stop 或 cancel 才能收尾。
            self._monitor_ticket = self._handler.send_async(MONITOR)
            print("  已开始流式输出，输入 stop 结束")
        elif text == "stop":
            self._stop_monitor()
        elif text == "alarm":
            self._device.inject_alarm()
            print("  已让设备主动上报一帧报警")
        elif text.startswith("raw "):
            raw_text = text[4:]
            print(f"  发送原始文本: {raw_text!r}")
            self._handler.send_async(raw_text)
        else:
            # 已注册命令的名字（或任意文本）都交给 send()：库先按声明匹配，
            # 匹配不到时按原始文本发送（route=RAW）。
            self._handler.send_async(text)

    def _stop_monitor(self) -> None:
        """停止流式输出：先抢发停止命令，再取消流式 ticket。"""
        if self._monitor_ticket is None:
            print("  当前没有在跑的流式命令")
            return
        # URGENT 优先级让停止命令插到队列前面，不用等普通命令发完。
        self._handler.send_async(STOP, priority=CommandPriority.URGENT)
        # cancel() 只影响本地队列：已经写出去的帧照旧会到达。
        self._monitor_ticket.cancel()
        self._monitor_ticket = None
        print("  已请求停止流式输出")

    def _on_result(self, result: Message[Any]) -> None:
        """命令终态槽：status 说明成败，data 是解析结果。"""
        name = "原始文本" if result.spec is None else result.spec.name
        print(f"  [结果] {name} 状态={result.status.value} 数据={result.data}")

    def _on_event(self, event: Message[Any]) -> None:
        """事件槽：流式帧、主动上报、以及没匹配上的回包都在这里。"""
        if event.spec is MONITOR:
            print(f"  [流] {event.data}")
        elif event.spec is ALARM:
            assert isinstance(event.data, Alarm)
            print(f"  [上报] 报警 {event.data.code}")
        else:
            print(f"  [未识别帧] {event.raw_frame!r}")

    def _pump(self, seconds: float) -> None:
        """泵 Qt 事件一小段时间，让刚才发出的请求有机会产生回包。

        真实 GUI 程序不需要这样做：信号槽本来就由 ``app.exec()`` 驱动。
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._application.processEvents()
            time.sleep(0.005)


if __name__ == "__main__":
    raise SystemExit(InteractiveConsole().run())
