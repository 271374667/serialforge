"""推荐的异步写法：扫描 → 连接 → 发送，闭包都在主线程（无硬件）。

三步各对应一个闭包：

1. ``scan_async(...).add_done_callback(on_devices)`` 拿到设备列表；
2. 在扫描闭包里 ``connect_async(devices[0]).add_done_callback(on_connected)``；
3. 连接成功后 ``send_async(VERSION).add_done_callback(on_done)`` 读结果。

示例手动泵事件只是为了在脚本里等闭包；真实 GUI 应用由 ``app.exec()`` 驱动。
失败分支、流式、上报等完整形态见 ``examples/advanced/async_callbacks.py``。

运行：``uv run python examples/simple/async_callback.py``
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.qt_app import QCoreApplication
from examples.virtual_device import VERSION, VersionInfo, VirtualDevice
from serialforge import DeviceInfo, Message, SerialForge
from serialforge.advanced import SerialConfig


class AsyncCallbackDemo:
    """用三段闭包完成一次“扫描 → 连接 → 读版本”。"""

    def __init__(self) -> None:
        """准备 Qt 应用、虚拟设备、门面与结果容器。"""
        self._application = QCoreApplication.instance() or QCoreApplication([])
        self._device = VirtualDevice()
        self._forge = SerialForge()
        self.VERSION = self._forge.add(VERSION)
        self._connected = False
        self._result: Message[VersionInfo] | None = None
        self._failed = False

    def run(self) -> int:
        """连上虚拟设备，读一次版本号。"""
        with self._device.attached():
            try:
                self._connect()
                self._ask_version()
            finally:
                self._forge.disconnect()
        if self._failed:
            print("没有找到设备")
            return 1
        return 0

    def _connect(self) -> None:
        """异步扫描；扫描闭包里再异步连接。"""

        def on_devices(devices: list[DeviceInfo]) -> None:
            if not devices:
                self._failed = True
                return

            def on_connected(device: DeviceInfo) -> None:
                self._connected = True
                print(f"已连接 {device.port}")

            self._forge.connect_async(devices[0]).add_done_callback(
                on_connected
            )

        self._forge.scan_async(
            baudrates=[self._device.baud_rate],
            probe=self.VERSION,
            vid_pid=self._device.vid_pid,
            serial=SerialConfig(settle_time_s=0),
        ).add_done_callback(on_devices)
        self._wait(lambda: self._connected or self._failed, 15.0)

    def _ask_version(self) -> None:
        """连接成功后异步发送一条命令，闭包在主线程读结果。"""

        def on_done(message: Message[VersionInfo]) -> None:
            self._result = message
            print(f"版本 {message.data[0].version}")

        self._forge.send_async(self.VERSION).add_done_callback(on_done)
        self._wait(lambda: self._result is not None)

    def _wait(
        self, predicate: Callable[[], bool], timeout_s: float = 3.0
    ) -> None:
        """泵事件直到条件成立；真实应用由 ``app.exec()`` 负责。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._application.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise TimeoutError("等待异步闭包超时")


if __name__ == "__main__":
    raise SystemExit(AsyncCallbackDemo().run())
