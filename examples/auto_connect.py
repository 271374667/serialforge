"""自动连接：给定参数与探测指令，让库自己找到并连上设备（无硬件）。

``scan()``、``connect()`` 与 ``send_sync()`` 都是**阻塞**的：一直等到结果或设备
就绪才返回，不需要事件泵；要异步就自己在工作线程里调用它们。

四件事：

1. ``DeviceProfile`` 上每个参数分别管什么（VID/PID、候选波特率、探测命令、超时）
2. VID/PID 可以省略：省略时不筛 USB 身份，全部串口都会被探测，靠回复确认设备
3. ``SerialForge.scan()`` 的 ``ALL`` 与 ``FIRST_MATCH`` 两种完成策略
4. 三种 ``connect()`` 入口：无参全自动 / 只给端口名 / 给已探测的 ``DeviceInfo``

门面没有扫描完成或进度信号：扫描结果由 ``scan()`` 的返回值给出。虚拟设备（含一台
VID/PID 不匹配的干扰设备）见 ``examples/virtual_device.py``。
运行：``uv run python examples/auto_connect.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.virtual_device import HELLO, VERSION, VirtualDevice
from serialforge import (
    DeviceInfo,
    DeviceProfile,
    ScanMode,
    SerialForge,
    SerialForgeError,
)
from serialforge.advanced import RuntimeConfig, SerialConfig
from serialforge.qt_core import QCoreApplication


class AutoConnectDemo:
    """用“参数 + 探测指令”找到设备，再演示三种连接入口。"""

    def __init__(self) -> None:
        """准备 Qt 应用、虚拟设备、干扰设备、profile 与门面。"""
        self._application = QCoreApplication.instance() or QCoreApplication([])
        self._device = VirtualDevice()
        # 一台 VID/PID 不匹配、也从不回应的干扰设备：用来看过滤和探测的效果。
        self._decoy = VirtualDevice.other_adapter()
        self._profile = self._build_profile(self._device.vid_pid)
        self._forge = SerialForge(self._profile)
        # 只注册本示例要用的两条命令；声明来自虚拟设备的指令集。
        self._forge.add(VERSION)
        self._forge.add(HELLO)

    def _build_profile(
        self, vid_pid: tuple[tuple[int, int], ...] | None
    ) -> DeviceProfile:
        """想找什么设备、怎么问它，全部写在这个对象里。"""
        return DeviceProfile(
            # VID/PID 可以省略：None 或空表示不做 USB 白名单过滤，全部串口都会
            # 被探测，完全靠探测指令的回复确认“这就是我的设备”。
            vid_pid=vid_pid,
            # 按顺序尝试；命中的波特率会缓存到应用数据目录，下次扫描优先复用。
            # 前面几档必然失败、各要等一个探测超时，所以别把列表铺太长。
            baudrates=[9600, 19200, self._device.baud_rate],
            # 探测命令必须是 SINGLE（默认）且有 pattern，
            # 且 pattern 不能匹配自己的请求文本。
            probe=VERSION,
            serial=SerialConfig(settle_time_s=0),
            # 整轮扫描的总超时：候选波特率越多、单次探测越慢，这里就要给得越大，
            # 否则还没试到正确波特率就被判定超时，结果是一个设备都找不到。
            runtime=RuntimeConfig(probe_total_timeout_s=10.0),
        )

    def run(self) -> int:
        """依次演示扫描参数、扫描模式与三种连接入口。"""
        with self._device.attached(self._decoy):
            self._scan(
                "省略 VID/PID（全部串口都会被探测）",
                self._build_profile(None),
            )
            found = self._scan(
                "给 VID/PID（名单外的端口直接跳过）",
                self._profile,
            )
            if not found:
                print("没有找到匹配的设备，请检查参数与接线")
                return 1
            self._scan(
                "ScanMode.FIRST_MATCH（拿到第一个成功者就取消其他探测）",
                self._profile,
                ScanMode.FIRST_MATCH,
            )
            target = found[0]
            # 入口 1：用已探测的 DeviceInfo，直接按已知波特率打开。
            self._connect_and_hello(target)
            # 入口 2：只给端口名——绕过 VID/PID 过滤，但仍要试候选波特率。
            self._connect_and_hello(target.port)
            # 入口 3：什么都不给——库自己筛选、探测、连接。
            self._connect_and_hello(None)
        return 0

    def _scan(
        self,
        title: str,
        profile: DeviceProfile,
        mode: ScanMode = ScanMode.ALL,
    ) -> list[DeviceInfo]:
        """跑一次阻塞扫描并打印结果；结果只从返回值来。"""
        print(f"\n=== 阻塞扫描：{title} ===")
        found = self._forge.scan(profile, mode=mode)
        # scan 用收到的 profile 配置门面；这里恢复本示例的主 profile。
        self._forge.configure(self._profile)
        print(f"  找到 {[item.port for item in found]}")
        return found

    def _connect_and_hello(self, target: str | DeviceInfo | None) -> None:
        """连接指定目标、同步取一条命令结果、断开。"""
        print(f"\n=== 连接入口：{self._describe(target)} ===")
        try:
            # 阻塞式连接：返回时设备已打开并初始化完成，可以立刻发命令。
            self._forge.connect(target or "")
            print(f"  已连接，状态={self._forge.state.value}")
            # send_sync 同样阻塞：返回的就是最终 Message，失败会抛异常。
            result = self._forge.send_sync(HELLO)
            print(f"  收到: {result.data}  原始帧: {result.raw_frames[0]!r}")
        except SerialForgeError as error:
            # 没找到设备、端口被占用、打开失败都在这里同步抛出。
            print(f"  连接失败: {error}")
        finally:
            # 同一个门面可以反复 connect/disconnect；不先断开不能再连。
            self._forge.disconnect()

    def _describe(self, target: str | DeviceInfo | None) -> str:
        """把三种 connect 目标写成一句人话，方便对照输出。"""
        if target is None:
            return "无参，自动筛选并按探测指令确认设备"
        if isinstance(target, DeviceInfo):
            return f"DeviceInfo({target.port}@{target.baudrate})，跳过重新探测"
        return f"端口名 {target}，绕过 VID/PID 过滤"


if __name__ == "__main__":
    raise SystemExit(AutoConnectDemo().run())
