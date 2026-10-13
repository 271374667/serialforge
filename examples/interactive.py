"""实时交互：用 ``while`` + ``input()`` 手动和虚拟设备对话（无硬件）。

输入命令后回车，示例会把请求发给设备并把回包打印出来：

===================  ========================================================
命令                 说明
===================  ========================================================
``Version``          单发单收
``ReadAll``          单发多收（``count=3``）
``Dump``             单发多收（``until=END``，行数由设备状态决定）
``monitor``          让设备持续吐帧，发 ``stop`` 或 ``status`` 结束
``stop``             停止采集（NO_REPLY，用 URGENT 抢发）
``status``           查设备状态；设备收到任何请求都会先停掉采集
``report``           开关周期上报：设备每 100 ms 主动推一条温度，超温再报警
``gain <n>``         设置增益：1..8 成功，其余回设备错误
``raw Ping``         发送未注册的原始文本；回包会作为未识别帧上报
``help``             打印这张表
``quit``             断开并退出
===================  ========================================================

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
    DUMP,
    MONITOR,
    READ_ALL,
    REPORT,
    REPORT_FAST_MS,
    SET_GAIN,
    STATUS,
    STOP,
    TELEMETRY,
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
    # 普通命令泵事件的时间：太短看不到回包，太长会让手感变差。
    PUMP_S = 0.3
    # 周期上报要等设备自己攒出超温，多泵一会儿才能看到报警。
    REPORT_PUMP_S = 0.9
    # 流式调用的本地期限：默认 3 秒会把采集掐断，示例显式放大。
    STREAM_TIMEOUT_S = 60.0
    HELP = (
        "  Version    单发单收\n"
        "  ReadAll    单发多收（count=3）\n"
        "  Dump       单发多收（until=END）\n"
        "  monitor    开始采集；设备持续吐帧，发 stop 或 status 结束\n"
        "  stop       停止采集（无回复）\n"
        "  status     查设备状态；任何请求都会先停掉采集\n"
        "  report     开关周期上报（设备每 100 ms 自己推温度）\n"
        "  gain <n>   设置增益：1..8 成功，其余回设备错误\n"
        "  raw Ping   发送未注册的原始文本（回包按未识别帧上报）\n"
        "  help       显示本帮助\n"
        "  quit       断开并退出"
    )

    def __init__(self) -> None:
        """准备 Qt 应用、虚拟设备、门面与信号槽。"""
        self._application = QCoreApplication.instance() or QCoreApplication([])
        self._device = VirtualDevice()
        self._forge = SerialForge(self._build_profile())
        # 正在跑的采集：需要留着 ticket 才能在停流时取消它。
        self._monitor_ticket: CommandTicket | None = None
        self._is_reporting: bool = False
        for declaration in self._device.declarations():
            self._forge.add(declaration)
        # 接收全部放在槽里：结果、事件（含未识别帧）都从信号来。
        self._forge.received.connect(
            lambda message: (
                self._on_result(message)
                if message.category is MessageCategory.COMMAND_RESULT
                else None
            )
        )
        self._forge.received.connect(
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
        with self._device.attached():
            try:
                # 阻塞式连接：返回时设备已就绪；失败同步抛 SerialForgeError。
                self._forge.connect()
                print(f"已连接虚拟设备（{self._forge.state.value}）")
                print("可用命令：")
                print(self.HELP)
                while self._read_once():
                    pass
            except SerialForgeError as error:
                print(f"连接失败: {error}")
                return 1
            finally:
                # 无论正常退出还是 EOF/Ctrl+C，都要断开，避免 I/O 线程悬挂。
                self._forge.disconnect()
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
        self._pump(self._dispatch(text))
        return True

    def _dispatch(self, text: str) -> float:
        """把一行输入翻译成库调用，返回这一条要泵多久事件。"""
        if not text:
            return 0.0
        word, _, argument = text.partition(" ")
        match word.lower():
            case "help":
                print(self.HELP)
            case "monitor":
                self._start_monitor()
            case "stop":
                self._stop_monitor()
            case "status":
                self._ask_status()
            case "report":
                return self._toggle_report()
            case "gain":
                self._send_gain(argument)
            case "dump":
                self._forge.send_async(DUMP)
            case "readall":
                self._forge.send_async(READ_ALL)
            case "version":
                self._forge.send_async(VERSION)
            case "raw":
                print(f"  发送原始文本: {argument!r}")
                self._forge.send_async(argument)
            case _:
                # 已注册命令的名字（或任意文本）都交给 send_async()：
                # 库先按声明匹配，匹配不到时按原始文本发送（route=RAW）。
                self._forge.send_async(text)
        return self.PUMP_S

    def _start_monitor(self) -> None:
        """开始采集：设备持续吐帧，直到下一条请求打断它。"""
        if self._monitor_ticket is not None:
            print("  采集已经在跑了")
            return
        # 流的存活期限来自这一次调用：默认 3 秒会把采集掐断。
        self._monitor_ticket = self._forge.send_async(
            MONITOR, timeout_s=self.STREAM_TIMEOUT_S
        )
        print("  已开始采集；发 stop 或 status 结束")

    def _stop_monitor(self) -> None:
        """停止采集：抢发停止命令；本地调用等它的结果回来再收尾。"""
        if self._monitor_ticket is None:
            print("  当前没有在跑的采集")
            return
        # URGENT 优先级让停止命令插到队列前面，不用等普通命令发完。
        self._forge.send_async(STOP, priority=CommandPriority.URGENT)
        print("  已请求停止采集")

    def _ask_status(self) -> None:
        """查设备状态：设备收到任何请求都会先停流，所以它也能打断采集。"""
        self._forge.send_async(STATUS)

    def _toggle_report(self) -> float:
        """开关周期上报：设备每 100 ms 主动推一条 TEMP，超温再补 ALARM。"""
        self._is_reporting = not self._is_reporting
        period_ms = REPORT_FAST_MS if self._is_reporting else 0
        self._forge.send_async(REPORT, params={"period_ms": period_ms})
        if not self._is_reporting:
            print("  已关闭周期上报")
            return self.PUMP_S
        print(f"  已开启周期上报（每 {period_ms} ms 一条），等它自己报警")
        return self.REPORT_PUMP_S

    def _send_gain(self, argument: str) -> None:
        """设置增益：1..8 成功，越界时设备回错误帧。"""
        self._forge.send_async(SET_GAIN, params={"value": argument or "0"})

    def _finish_monitor(self) -> None:
        """结束本地流式调用；设备端由它收到的下一条请求停流。"""
        if self._monitor_ticket is None:
            return
        # cancel() 只影响本地调用：已经写出去的帧照旧会到达。
        self._monitor_ticket.cancel()
        self._monitor_ticket = None

    def _on_result(self, result: Message[Any]) -> None:
        """命令终态槽：status 说明成败，data 是解析结果。"""
        name = "原始文本" if result.spec is None else result.spec.name
        detail = f" 错误={result.error_message}" if result.error_message else ""
        print(
            f"  [结果] {name} 状态={result.status.value} "
            f"数据={result.data}{detail}"
        )
        if result.spec is STOP or result.spec is STATUS:
            # 设备因为收到这条命令而停流，本地流式调用跟着收尾。
            self._finish_monitor()

    def _on_event(self, event: Message[Any]) -> None:
        """事件槽：流式帧、主动上报、以及没匹配上的回包都在这里。"""
        if event.spec is MONITOR:
            print(f"  [流] {event.data}")
        elif event.spec is TELEMETRY:
            print(f"  [上报] {event.data}")
        elif event.spec is ALARM:
            alarm = event.data[0]
            assert isinstance(alarm, Alarm)
            print(f"  [报警] 报警码 {alarm.code}")
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
