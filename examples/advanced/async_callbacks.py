"""推荐用法：命令集中声明 → 异步扫描连接 → send_async 主线程闭包（无硬件）。

这是 serialforge 在 GUI 应用里的标准骨架，三步固定下来：

1. 声明集中一处：建好 ``SerialForge()`` 后立刻把协议里的命令全部 ``add(...)``
   存成属性，每个命令只创建一次，之后只引用这个对象。
2. 扫描与连接都在后台：``scan_async(baudrates=..., probe=..., vid_pid=...)``
   的闭包在主线程收到设备列表，再用 ``connect_async(devices[0])`` 连接；
   给 ``vid_pid`` 时名单外的端口在打开前就会被跳过，扫描更快。
3. 发送与消费写在同一个函数里：``send_async(命令).add_done_callback(闭包)``，
   闭包统一排队到 Qt 应用主线程，可以直接更新界面。

示例同时覆盖三条异步路径：

- 成功：闭包拿到 ``Message``，与调用句柄的 ``result()`` 是同一个对象。
- 失败：默认 3 秒超时/失败不执行成功闭包，用 ``call.result()`` 重抛类型化异常。
- 流式：``STREAM`` 只能异步发送；解析帧不进入 ``received``，示例从
  ``received`` 的非空 ``raw_data`` 观察流字节，用 ``cancel()`` 收尾。

虚拟设备与它的指令集见 ``examples/virtual_device.py``。
运行：``uv run python examples/advanced/async_callbacks.py``
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# 允许直接 ``python examples/advanced/async_callbacks.py`` 时找到项目根。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.qt_app import QCoreApplication
from examples.virtual_device import (
    MONITOR,
    STOP,
    VERSION,
    VersionInfo,
    VirtualDevice,
)
from serialforge import (
    DeviceInfo,
    Message,
    SerialForge,
    SerialForgeError,
)
from serialforge.advanced import CommandPriority, SerialConfig
from serialforge.errors import CommandExecutionError


class AsyncCallbackDemo:
    """演示推荐的主线程闭包消费方式。"""

    def __init__(self) -> None:
        """准备 Qt 应用、虚拟设备、门面与信号收集容器。"""
        # SerialForge 构造时就会检查 Qt 应用是否存在，所以先建它。
        self._application = QCoreApplication.instance() or QCoreApplication([])
        self._device = VirtualDevice()
        # 不再手工拼 DeviceProfile：扫描时把波特率、探针和 VID/PID 一起给全。
        self._forge = SerialForge()
        # ① 声明集中一处：命令表和这几十行一一对应，发送处只引用对象。
        self.VERSION = self._forge.add(VERSION)
        self.MONITOR = self._forge.add(MONITOR)
        self.STOP = self._forge.add(STOP)
        # 闭包收到的 Message、以及非空原始接收行。
        self._callbacks: list[Message[Any]] = []
        self._raw: list[Message[Any]] = []
        self._forge.received.connect(self._raw.append)
        self._connected = False
        self._connect_error: bool = False

    def run(self) -> int:
        """连上设备，依次演示成功回调、失败不回调与 STREAM 收尾。"""
        with self._device.attached():
            try:
                self._connect()
                self._demo_done_callback()
                self._demo_failure_keeps_callback_silent()
                self._demo_stream_cancel()
            except SerialForgeError as error:
                print(f"[连接失败] {error}")
                return 1
            finally:
                # 退出前断开：它会等 I/O 线程停下来，避免解释器结束时线程悬挂。
                self._forge.disconnect()
        print("\n[完成] 三条异步路径都演示过了")
        return 0

    def _connect(self) -> None:
        """② 异步扫描 + 异步连接：和点击“连接设备”按钮后的流程完全一致。"""
        self._connected = False
        self._connect_error = False

        def on_scan_done(devices: list[DeviceInfo]) -> None:
            """扫描完成：闭包在主线程执行，可以直接改界面。"""
            if not devices:
                self._connect_error = True
                return

            def on_connect_done(device: DeviceInfo) -> None:
                """连接完成：参数是已打开并初始化好的设备。"""
                self._connected = True
                print(f"[连接] {device.port}@{device.baudrate}")

            self._forge.connect_async(devices[0]).add_done_callback(
                on_connect_done
            )

        self._forge.scan_async(
            baudrates=[self._device.baud_rate],
            probe=self.VERSION,
            # 只打开这个 USB 身份的端口；省略则探测全部串口，靠探针确认。
            vid_pid=self._device.vid_pid,
            # 真机请保留默认的 0.3 秒开串口稳定等待；示例设为 0 只为省时间。
            serial=SerialConfig(settle_time_s=0),
        ).add_done_callback(on_scan_done)
        # 脚本里自己泵事件等结果；真实应用由 app.exec() 驱动这些闭包。
        self._wait_until(
            lambda: self._connected or self._connect_error,
            timeout_s=15.0,
        )
        if self._connect_error:
            raise SerialForgeError("扫描没有找到设备")

    def _demo_done_callback(self) -> None:
        """1. 成功结果交给主线程闭包，闭包与 result() 是同一个 Message。"""
        print("\n=== 1. add_done_callback 消费成功结果 ===")
        self._callbacks.clear()

        def on_done(message: Message[VersionInfo]) -> None:
            """回调注解按声明的 result_type 写，data 是 list[VersionInfo]。"""
            self._callbacks.append(message)

        # context 是每次调用独立的只读快照，闭包里可以按它回填界面。
        call = self._forge.send_async(self.VERSION, context={"row": 7})
        call.add_done_callback(on_done)
        self._wait_until(lambda: bool(self._callbacks))
        message = self._callbacks[0]
        print(f"  回调: {message.data}  context={message.context['row']}")
        print(f"  done={call.done}  callback_expired={call.callback_expired}")
        # 结果已经成功后再补注册：闭包同样排队回放，result() 不阻塞。
        call.add_done_callback(on_done)
        self._wait_until(lambda: len(self._callbacks) >= 2)
        same = self._callbacks[1] is call.result()
        print(f"  闭包 / result() 同一对象: {same}")
        if self._raw:
            print(f"  同期原始接收行: {self._raw[-1].raw_data!r}")

    def _demo_failure_keeps_callback_silent(self) -> None:
        """2. 超时不执行成功闭包，失败要用 result() 取。"""
        print("\n=== 2. 超时不回调，失败从 result() 重抛 ===")
        self._callbacks.clear()
        # 声明写在使用方这一侧；虚拟设备不认识这条请求，永远等不到回复。
        silent = self._forge.add("NoAnswer", r"ANSWER \d+")

        def on_done(message: Message[str]) -> None:
            """这条闭包只会在成功时执行；超时不会进来。"""
            self._callbacks.append(message)

        # 0.4 秒是本地期限：闭包注册成功，但超时后不会被调用。
        call = self._forge.send_async(silent, timeout_s=0.4)
        call.add_done_callback(on_done)
        self._wait_until(lambda: call.done)
        print(f"  done={call.done}  成功闭包执行次数={len(self._callbacks)}")
        try:
            call.result()
        except CommandExecutionError as error:
            print(f"  result() 重抛 {type(error).__name__}")

    def _demo_stream_cancel(self) -> None:
        """3. STREAM 原始帧从 received 来，cancel() 给出终态。"""
        print("\n=== 3. STREAM 原始帧从 received 来，cancel() 收尾 ===")
        self._raw.clear()
        # 流的存活期限来自这一次调用：默认 3 秒会把它掐断。
        ticket = self._forge.send_async(self.MONITOR, timeout_s=60.0)
        self._wait_until(lambda: len(self._stream_lines()) >= 3)
        # STOP 是 NO_REPLY，能在流里插队发出，让设备停止吐帧。
        stop = self._forge.send_async(
            self.STOP, priority=CommandPriority.URGENT
        )
        self._wait_until(lambda: stop.done)
        stop.result()
        # cancel() 只影响本地调用：已经写出去的帧照旧会到达 received。
        ticket.cancel()
        self._wait_until(lambda: ticket.done)
        for line in self._stream_lines():
            print(f"  流式原始帧: {line.raw_data!r}")
        print(f"  流式命令终态: {self._status(ticket)}")

    def _stream_lines(self) -> list[Message[Any]]:
        """按非空 raw_data 的前缀筛流式帧，received 不提供解析值。"""
        return [
            message
            for message in self._raw
            if message.raw_data.startswith(b"ADC ")
        ]

    @staticmethod
    def _status(call: Any) -> str:
        """从调用句柄取终态：成功读 status，失败读异常的 status。"""
        if call.exception is not None:
            return str(call.exception.status.value)
        return str(call.result().status.value)

    def _wait_until(
        self,
        predicate: Callable[[], bool],
        timeout_s: float = 3.0,
    ) -> None:
        """泵 Qt 事件直到条件成立（真实应用由 ``app.exec()`` 负责这件事）。

        扫描、连接、结果与原始接收都由后台线程发出、在主线程排队，必须转一次
        事件循环才会执行闭包；示例里手动 ``processEvents()`` 只是为了让顺序
        一眼可见。
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._application.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise TimeoutError("等待超时：异步回调或流式帧没有按预期到达")


if __name__ == "__main__":
    raise SystemExit(AsyncCallbackDemo().run())
