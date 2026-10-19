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
``raw Ping``         发送未注册的原始文本；回包按原始字节打印
``help``             打印这张表
``quit``             断开并退出
===================  ========================================================

虚拟设备与它完整指令集见 ``examples/virtual_device.py``。
运行：``uv run python examples/advanced/interactive.py``

这是“脚本/工作线程”形态：``connect_sync()`` 阻塞到设备就绪，返回后就能发命令。
GUI 应用推荐 ``scan_async()`` → ``connect_async()`` 的闭包写法（见
``examples/advanced/async_callbacks.py``），不要在主线程阻塞。

命令终态从调用句柄取：成功读 ``Message.data``，失败读 ``result()`` 重抛的异常。
``received`` 只发非空原始字节，所以流式帧、主动上报和未识别回包都按原始行打印。
``input()`` 会阻塞 Qt 事件循环，所以示例在每次输入后手动泵一小段时间，让回包
有机会送达。真实 GUI 程序不要让 ``input()`` 占住事件线程，应该把收发做成信号槽。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.qt_app import QCoreApplication
from examples.virtual_device import (
    DUMP,
    MONITOR,
    READ_ALL,
    REPORT,
    REPORT_FAST_MS,
    SET_GAIN,
    STATUS,
    STOP,
    VERSION,
    VirtualDevice,
)
from serialforge import (
    DeviceProfile,
    Message,
    SerialForge,
    SerialForgeError,
)
from serialforge.advanced import CommandCall, CommandPriority, SerialConfig
from serialforge.errors import CommandExecutionError


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
        "  raw Ping   发送未注册的原始文本（回包按原始字节打印）\n"
        "  help       显示本帮助\n"
        "  quit       断开并退出"
    )

    def __init__(self) -> None:
        """准备 Qt 应用、虚拟设备、门面与信号槽。"""
        self._application = QCoreApplication.instance() or QCoreApplication([])
        self._device = VirtualDevice()
        self._forge = SerialForge(self._build_profile())
        # 还没结束的调用：终态统一从这些句柄取，失败读 result() 的异常。
        self._calls: list[CommandCall[Any]] = []
        # 正在跑的采集：需要留着句柄才能在停流时取消它。
        self._monitor_ticket: CommandCall[Any] | None = None
        self._is_reporting: bool = False
        for declaration in self._device.declarations():
            self._forge.add(declaration)
        # received 只发非空原始字节：流式帧、上报和未识别回包都在这里观察。
        self._forge.received.connect(self._on_raw)

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
                self._forge.connect_sync()
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
                self._submit(DUMP)
            case "readall":
                self._submit(READ_ALL)
            case "version":
                self._submit(VERSION)
            case "raw":
                print(f"  发送原始文本: {argument!r}")
                self._submit(argument)
            case _:
                # 已注册命令的名字（或任意文本）都交给 send_async()：
                # 库先按声明匹配，匹配不到时按原始文本发送（route=RAW）。
                self._submit(text)
        return self.PUMP_S

    def _submit(self, target: Any, **options: Any) -> CommandCall[Any]:
        """异步发送并把调用句柄记下来，终态由 _report_finished_calls 打印。"""
        call = self._forge.send_async(target, **options)
        self._calls.append(call)
        return call

    def _start_monitor(self) -> None:
        """开始采集：设备持续吐帧，直到下一条请求打断它。"""
        if self._monitor_ticket is not None:
            print("  采集已经在跑了")
            return
        # 流的存活期限来自这一次调用：默认 3 秒会把采集掐断。
        self._monitor_ticket = self._submit(
            MONITOR, timeout_s=self.STREAM_TIMEOUT_S
        )
        print("  已开始采集；发 stop 或 status 结束")

    def _stop_monitor(self) -> None:
        """停止采集：抢发停止命令；本地调用等它的结果回来再收尾。"""
        if self._monitor_ticket is None:
            print("  当前没有在跑的采集")
            return
        # URGENT 优先级让停止命令插到队列前面，不用等普通命令发完。
        self._submit(STOP, priority=CommandPriority.URGENT)
        print("  已请求停止采集")

    def _ask_status(self) -> None:
        """查设备状态：设备收到任何请求都会先停流，所以它也能打断采集。"""
        self._submit(STATUS)

    def _toggle_report(self) -> float:
        """开关周期上报：设备每 100 ms 主动推一条 TEMP，超温再补 ALARM。"""
        self._is_reporting = not self._is_reporting
        period_ms = REPORT_FAST_MS if self._is_reporting else 0
        self._submit(REPORT, params={"period_ms": period_ms})
        if not self._is_reporting:
            print("  已关闭周期上报")
            return self.PUMP_S
        print(f"  已开启周期上报（每 {period_ms} ms 一条），等它自己报警")
        return self.REPORT_PUMP_S

    def _send_gain(self, argument: str) -> None:
        """设置增益：1..8 成功，越界时设备回错误帧。"""
        self._submit(SET_GAIN, params={"value": argument or "0"})

    def _finish_monitor(self) -> None:
        """结束本地流式调用；设备端由它收到的下一条请求停流。"""
        if self._monitor_ticket is None:
            return
        # cancel() 只影响本地调用：已经写出去的帧照旧会到达。
        self._monitor_ticket.cancel()
        self._monitor_ticket = None

    def _after_terminal(self, spec: Any) -> None:
        """STOP/STATUS 会让设备停流，本地流式调用跟着收尾。"""
        if spec is STOP or spec is STATUS:
            self._finish_monitor()

    def _report_finished_calls(self) -> None:
        """打印已结束调用的终态：成功读 Message，失败读异常里的 status。"""
        for call in tuple(self._calls):
            if not call.done:
                continue
            self._calls.remove(call)
            try:
                message = call.result()
            except CommandExecutionError as error:
                name = "原始文本" if error.spec is None else error.spec.name
                print(f"  [结果] {name} 状态={error.status.value} 错误={error}")
                self._after_terminal(error.spec)
            else:
                spec = message.spec
                name = "原始文本" if spec is None else spec.name
                print(
                    f"  [结果] {name} 状态={message.status.value} "
                    f"数据={message.data}"
                )
                self._after_terminal(spec)

    def _on_raw(self, message: Message[Any]) -> None:
        """原始接收槽：received 只有非空 raw_data，没有解析值。"""
        text = message.raw_data.decode("utf-8", errors="replace").strip()
        if text.startswith("ADC "):
            print(f"  [流] {text}")
        elif text.startswith("TEMP "):
            print(f"  [上报] {text}")
        elif text.startswith("ALARM "):
            print(f"  [报警] {text}")
        else:
            print(f"  [原始] {text}")

    def _pump(self, seconds: float) -> None:
        """泵 Qt 事件一小段时间，让回包和调用终态有机会送达。

        真实 GUI 程序不需要这样做：信号槽本来就由 ``app.exec()`` 驱动。
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._application.processEvents()
            self._report_finished_calls()
            time.sleep(0.005)
        self._report_finished_calls()


if __name__ == "__main__":
    raise SystemExit(InteractiveConsole().run())
