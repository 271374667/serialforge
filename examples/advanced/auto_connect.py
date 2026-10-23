"""自动连接：给参数与探测指令，让库自己找到并连上设备（无硬件）。

推荐形态是两段异步：``scan_async(...).add_done_callback(扫描闭包)``，在扫描闭包里
再 ``connect_async(devices[0]).add_done_callback(连接闭包)``。两次调用都在
后台线程执行，闭包回到 Qt 应用主线程，界面全程不卡；脚本里也可以像本示例这样，
用好句柄的 ``done`` / ``result()`` 顺序跑完。

四件事：

1. 扫描参数分别管什么：候选波特率、探测命令、总超时
2. VID/PID 可以省略：省略时全部串口都会被探测，靠回复确认设备；给出时名单外的
   端口在打开前就被跳过，扫描明显更快
3. ``scan_async()`` 的 ``ALL`` 与 ``FIRST_MATCH`` 两种完成策略
4. 三种 ``connect_async()`` 入口：无参全自动 / ``list_devices()`` 选出的
   未探测 ``DeviceInfo`` / 已探测的 ``DeviceInfo``

门面没有扫描完成或进度信号：结果由 ``ScanCall`` 的闭包或 ``result()`` 交付。
虚拟设备（含一台 VID/PID 不匹配的干扰设备）见 ``examples/virtual_device.py``。
运行：``uv run python examples/advanced/auto_connect.py``
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.qt_app import QCoreApplication
from examples.virtual_device import HELLO, VERSION, VirtualDevice
from serialforge import (
    DeviceInfo,
    Message,
    ScanMode,
    SerialForge,
    SerialForgeError,
)
from serialforge.advanced import RuntimeConfig, SerialConfig


class AutoConnectDemo:
    """用“参数 + 探测指令”找到设备，再演示三种异步连接入口。"""

    def __init__(self) -> None:
        """准备 Qt 应用、虚拟设备、干扰设备与门面。"""
        self._application = QCoreApplication.instance() or QCoreApplication([])
        self._device = VirtualDevice()
        # 一台 VID/PID 不匹配、也从不回应的干扰设备：用来看过滤和探测的效果。
        self._decoy = VirtualDevice.other_adapter()
        # 不需要先拼 DeviceProfile：scan_async 的字段形式就是完整配置。
        self._forge = SerialForge()
        # ① 声明集中一处：探针与握手命令都只声明一次，之后只引用对象。
        self.VERSION = self._forge.add(VERSION)
        self.HELLO = self._forge.add(HELLO)

    def run(self) -> int:
        """依次演示扫描参数、扫描模式与三种连接入口。"""
        with self._device.attached(self._decoy):
            self._scan(
                "省略 VID/PID（全部串口都会被探测）",
                vid_pid=None,
            )
            found = self._scan(
                "给 VID/PID（名单外的端口打开前就被跳过，扫描更快）",
                vid_pid=self._device.vid_pid,
            )
            if not found:
                print("没有找到匹配的设备，请检查参数与接线")
                return 1
            self._scan(
                "ScanMode.FIRST_MATCH（拿到第一个成功者就取消其他探测）",
                vid_pid=self._device.vid_pid,
                mode=ScanMode.FIRST_MATCH,
            )
            target = found[0]
            # 入口 1：用已探测的 DeviceInfo，直接按已知波特率打开。
            self._connect_and_hello(
                target,
                f"DeviceInfo({target.port}@{target.baudrate})，跳过重新探测",
            )
            # 入口 2：先 list_devices() 列出全部可用串口（不开口、不探测、
            # 不按 VID/PID 过滤），再把选中的 DeviceInfo 传给连接接口。
            # 端口名会随插拔变化，库不再接受直接传端口名字符串。未探测的
            # DeviceInfo 没有波特率，会按当前配置的首档波特率直接打开，
            # 所以这里只配置已知的那一档；不确定波特率就先 scan。
            listed = self._forge.list_devices()
            print(f"  可用串口: {[item.port for item in listed]}")
            pick = next(
                (item for item in listed if item.port == target.port), None
            )
            if pick is None:
                print("  目标端口不在可用列表中，跳过该入口")
            else:
                self._forge.configure(
                    baudrates=[self._device.baud_rate],
                    probe=self.VERSION,
                    serial=SerialConfig(settle_time_s=0),
                )
                self._connect_and_hello(
                    pick,
                    f"list_devices() 选出的 DeviceInfo({pick.port})，"
                    "按已知波特率直接打开",
                )
            # 入口 3：什么都不给——库自己筛选、探测、连接。
            self._connect_and_hello(
                None,
                "无参，自动筛选并按探测指令确认设备",
            )
        return 0

    def _scan(
        self,
        title: str,
        *,
        vid_pid: Sequence[tuple[int, int]] | None,
        mode: ScanMode = ScanMode.ALL,
    ) -> list[DeviceInfo]:
        """跑一次异步扫描并打印结果；脚本里用句柄等结果。"""
        print(f"\n=== 异步扫描：{title} ===")
        call = self._forge.scan_async(
            # 按顺序尝试；命中的波特率会缓存，下次扫描优先复用。前面几档必然
            # 失败、各要等一个探测窗口，所以别把列表铺太长。
            baudrates=[9600, 19200, self._device.baud_rate],
            # 探测命令必须是 SINGLE（默认）且 pattern 不能匹配自己的请求文本。
            probe=self.VERSION,
            # 空元组 = 不过滤 USB 身份；给出时只保留名单内的端口。
            vid_pid=vid_pid or (),
            mode=mode,
            serial=SerialConfig(settle_time_s=0),
            # 整轮扫描的总超时：候选波特率越多，这里就要给得越大。
            runtime=RuntimeConfig(probe_total_timeout_s=10.0),
        )
        # GUI 里写 call.add_done_callback(on_devices)；
        # 脚本这里等句柄结束再看结果。
        self._wait_until(lambda: call.done, timeout_s=30.0)
        found = call.result()
        print(f"  找到 {[item.port for item in found]}")
        return found

    def _connect_and_hello(
        self,
        target: DeviceInfo | list[DeviceInfo] | None,
        label: str,
    ) -> None:
        """异步连接指定目标，再用 send_async + 闭包取一条命令结果，然后断开。"""
        print(f"\n=== 连接入口：{label} ===")
        try:
            connect_call = self._forge.connect_async(target)
            self._wait_until(lambda: connect_call.done, timeout_s=30.0)
            device = connect_call.result()
            print(f"  已连接 {device.port}，状态={self._forge.state.value}")
            # ② 发送与消费写在同一个函数里：闭包在主线程收到 Message。
            results: list[Message[Any]] = []
            self._forge.send_async(self.HELLO).add_done_callback(results.append)
            self._wait_until(lambda: bool(results))
            message = results[0]
            print(f"  收到: {message.data}  原始帧: {message.raw_frame!r}")
        except SerialForgeError as error:
            # 没找到设备、端口被占用、打开失败都在 result() 里重抛。
            print(f"  连接失败: {error}")
        finally:
            # 同一个门面可以反复连接/断开；不先断开不能再连。
            self._forge.disconnect()

    def _wait_until(
        self,
        predicate: Callable[[], bool],
        timeout_s: float = 3.0,
    ) -> None:
        """泵 Qt 事件直到条件成立（真实应用由 ``app.exec()`` 驱动闭包）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._application.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise TimeoutError("等待超时：扫描、连接或命令结果没有按预期到达")


if __name__ == "__main__":
    raise SystemExit(AutoConnectDemo().run())
