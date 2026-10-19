"""快速启动：一个文件看完全部“从设备拿信息”的方式（无硬件）。

本示例只写 serialforge 的事：建 Qt 应用、用门面集中声明命令、异步扫描连接、
发指令、收结果。虚拟设备与它的指令集（Version / Hello / ReadAll / Dump /
Monitor / Stop / Report / SetGain / Status / TEMP / ALARM）见
``examples/virtual_device.py``；真机换成自己的 VID/PID 与波特率即可。

覆盖的五种取信息方式：

1. 单发单收（SINGLE）：一条请求 → 一条回复
2. 单发多收（MULTI）：一条请求 → 多条回复（用 ``count`` 或 ``until`` 结束）
3. 流式输出（STREAM）：设备持续吐帧，原始字节逐行从 ``received`` 出来
4. 主动上报（Spec）：设备没被问也会推数据，同样按非空原始字节观察
5. 无回复（NO_REPLY）：写完就结束，不等回复

连接走推荐形态：``scan_async(...)`` 的闭包拿设备列表，再在闭包里
``connect_async(devices[0])``，全程不阻塞。示例里手动泵事件只是为了在脚本中
等这些主线程闭包和信号；真实应用由 ``app.exec()`` 驱动。

运行：``uv run python examples/advanced/quick_start.py``
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# 允许直接 ``python examples/advanced/quick_start.py`` 运行时找到项目根。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.qt_app import QCoreApplication
from examples.virtual_device import (
    DUMP,
    MONITOR,
    READ_ALL,
    REPORT,
    REPORT_FAST_MS,
    STOP,
    VERSION,
    VirtualDevice,
)
from serialforge import (
    DeviceInfo,
    Message,
    SerialForge,
    SerialForgeError,
    Spec,
)
from serialforge.advanced import CommandPriority, SerialConfig
from serialforge.errors import CommandExecutionError


class QuickStartDemo:
    """按顺序演示五种取信息方式，全部逻辑都收在这个类里。"""

    def __init__(self) -> None:
        """准备 Qt 应用、虚拟设备、门面与信号收集容器。"""
        # SerialForge 构造时就会检查 Qt 应用是否存在，所以先建它。
        self._application = QCoreApplication.instance() or QCoreApplication([])
        # 虚拟设备的指令集见 examples/virtual_device.py 顶部的表格。
        self._device = VirtualDevice()
        # 扫描时再给波特率、探针和 VID/PID，不必提前拼 DeviceProfile。
        self._forge = SerialForge()
        self._connected = False
        self._connect_error: bool = False
        # received 只发非空原始字节；命令结果从调用句柄/回调取，别在这里等。
        self._received: list[Message[Any]] = []
        # 命令必须先注册再发送，而且要用同一份声明对象（库不复制声明）。
        for declaration in self._device.declarations():
            self._forge.add(declaration)
        self._forge.received.connect(self._received.append)

    def _connect(self) -> None:
        """推荐形态：异步扫描设备，再在扫描闭包里异步连接。"""
        self._connected = False
        self._connect_error = False

        def on_scan_done(devices: list[DeviceInfo]) -> None:
            """扫描完成：无匹配时保持断开，有匹配就接着连接第一个。"""
            if not devices:
                self._connect_error = True
                return

            def on_connect_done(device: DeviceInfo) -> None:
                """连接完成：设备已打开并初始化好，可以直接发送。"""
                self._connected = True
                print(f"[连接] {device.port} 状态={self._forge.state.value}")

            self._forge.connect_async(devices[0]).add_done_callback(
                on_connect_done
            )

        self._forge.scan_async(
            baudrates=[self._device.baud_rate],
            # 探测命令必须是 SINGLE 命令，而且 pattern 不能匹配自己的请求文本。
            probe=VERSION,
            # 只探测这个 USB 身份的端口；省略则扫描全部串口。
            vid_pid=self._device.vid_pid,
            # 真机请保留默认的 0.3 秒开串口稳定等待；示例把它设为 0 只为省时间。
            serial=SerialConfig(settle_time_s=0),
        ).add_done_callback(on_scan_done)
        # 脚本里手动泵事件等连接完成；真实应用由 app.exec() 驱动。
        self._wait_until(
            lambda: self._connected or self._connect_error,
            timeout_s=15.0,
        )
        if self._connect_error:
            raise SerialForgeError("扫描没有找到设备")

    def run(self) -> int:
        """连上设备，依次演示五种取信息方式，返回进程退出码。"""
        with self._device.attached():
            try:
                self._connect()
                self._demo_single()
                self._demo_multi()
                self._demo_stream()
                self._demo_event()
                self._demo_no_reply()
            except SerialForgeError as error:
                print(f"[连接失败] {error}")
                return 1
            finally:
                # 退出前断开：它会等 I/O 线程停下来，避免解释器结束时线程悬挂。
                self._forge.disconnect()
        print("\n[完成] 五种取信息方式都跑过了")
        return 0

    def _demo_single(self) -> None:
        """1. 单发单收：一条请求对应一条回复。"""
        print("\n=== 1. 单发单收（SINGLE）===")
        result = self._send(VERSION)
        print(f"  请求文本: {VERSION.request!r}")
        print(f"  解析结果: {result.data}  原始帧: {result.raw_frames[0]!r}")

    def _demo_multi(self) -> None:
        """2. 单发多收：MULTI 把多帧收进一个结果里。"""
        print("\n=== 2. 单发多收（MULTI）===")
        # 定数收尾：count=3 收到第三帧就结束。
        result = self._send(READ_ALL)
        # 声明了 result_type=dict，每帧是一个命名组字典，整体是 list。
        for index, frame in enumerate(result.data, start=1):
            print(f"  count=3 第 {index} 帧: {frame}")
        # 定界收尾：行数由设备状态决定，所以用 until="END" 收尾。
        dumped = self._send(DUMP)
        print(f"  until=END 收到 {len(dumped.data)} 行样本")
        print(f"  最后一帧: {dumped.raw_frames[-1]!r}")

    def _demo_stream(self) -> None:
        """3. 流式输出：STREAM 的原始帧从 received 观察。"""
        print("\n=== 3. 流式输出（STREAM）===")
        self._received.clear()
        # 流的存活期限来自这一次调用：默认 3 秒会把长一点的采集掐断。
        ticket = self._forge.send_async(MONITOR, timeout_s=60.0)
        self._wait_until(lambda: len(self._lines(b"ADC ")) >= 3)
        # 停止命令要插到普通命令前面，所以用 URGENT 优先级。
        stop = self._forge.send_async(STOP, priority=CommandPriority.URGENT)
        self._wait_until(lambda: stop.done)
        stop.result()
        # cancel() 让流式命令自身也有明确的终态（CANCELLED），否则它会一直挂着。
        ticket.cancel()
        self._wait_until(lambda: ticket.done)
        for line in self._lines(b"ADC "):
            print(f"  原始帧: {line.raw_data!r}")
        error = ticket.exception
        assert isinstance(error, CommandExecutionError)
        print(f"  流式命令终态: {error.status.value}")

    def _demo_event(self) -> None:
        """4. 主动上报：设备到点自己推数据，没人问它。"""
        print("\n=== 4. 主动上报（设备自己发的事件）===")
        self._received.clear()
        # Report 100：让设备每 100 毫秒推一条 TEMP；它自己判断超温再补 ALARM。
        self._send(REPORT, params={"period_ms": REPORT_FAST_MS})
        self._wait_until(lambda: bool(self._lines(b"ALARM ")))
        for line in self._lines(b"TEMP ")[:3]:
            print(f"  上报原始帧: {line.raw_data!r}")
        alarm = self._lines(b"ALARM ")[0]
        print(f"  报警原始帧: {alarm.raw_data!r}")
        print("  提示：received 只发非空原始字节，解析值走声明与调用结果")

    def _demo_no_reply(self) -> None:
        """5. 无回复：写完立即结束，不等设备回答。"""
        print("\n=== 5. 无回复（NO_REPLY）===")
        # Report 0 关掉刚才打开的上报；无回复指令写完就算成功。
        result = self._send(REPORT, params={"period_ms": 0})
        print(f"  终态: {result.status.value}  数据: {result.data}")
        print(f"  已写出字节: {result.sent!r}")

    def _send(
        self,
        target: Spec[Any] | str,
        *,
        params: dict[str, object] | None = None,
    ) -> Message[Any]:
        """异步发送并用主线程闭包取结果；脚本里泵事件等它到达。"""
        callbacks: list[Message[Any]] = []
        call = self._forge.send_async(target, params=params or {})
        call.add_done_callback(callbacks.append)
        self._wait_until(lambda: bool(callbacks))
        return callbacks[0]

    def _lines(self, prefix: bytes) -> list[Message[Any]]:
        """按非空 raw_data 的前缀筛原始接收行。"""
        return [
            message
            for message in self._received
            if message.raw_data.startswith(prefix)
        ]

    def _wait_until(
        self,
        predicate: Callable[[], bool],
        timeout_s: float = 3.0,
    ) -> None:
        """泵 Qt 事件直到条件成立（只用于等回调与原始接收）。

        扫描/连接回调、命令回调和 ``received`` 都由后台线程发出、在主线程
        排队，必须转一次事件循环才会执行；真实应用由 ``app.exec()`` 负责。
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._application.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise TimeoutError("等待超时：设备没有按预期返回数据")


if __name__ == "__main__":
    raise SystemExit(QuickStartDemo().run())
