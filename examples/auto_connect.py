"""自动连接：给定参数与探测指令，让库自己找到并连上设备（无硬件）。

``find()`` 与 ``connect()`` 现在都是**阻塞**的：一直等到结果或设备就绪才返回，
不需要事件泵；要异步就自己在工作线程里调用它们。

四件事：

1. ``DeviceProfile`` 上每个参数分别管什么（VID/PID、候选波特率、探测命令、超时）
2. VID/PID 可以省略：省略时不筛 USB 身份，全部串口都会被探测，靠回复确认设备
3. ``DeviceFinder.find()`` 的 ``ALL`` 与 ``FIRST_MATCH`` 两种完成策略
4. ``handler.connect()`` 的三种入口：无参全自动 / 只给端口名 /
   给已探测的 ``DeviceInfo``

虚拟设备（含一台 VID/PID 不匹配的干扰设备）见 ``examples/virtual_device.py``。
运行：``uv run python examples/auto_connect.py``
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication

from examples.virtual_device import HELLO, VERSION, VirtualDevice
from serialforge import (
    CommandResult,
    CommandSpec,
    DeviceFinder,
    DeviceInfo,
    DeviceProfile,
    ScanMode,
    SerialForgeError,
    SerialHandler,
)
from serialforge.advanced import RuntimeConfig, SerialConfig


class AutoConnectDemo:
    """用“参数 + 探测指令”找到设备，再演示三种连接入口。"""

    def __init__(self) -> None:
        """准备 Qt 应用、虚拟设备、干扰设备、profile 与 handler。"""
        self._application = QCoreApplication.instance() or QCoreApplication([])
        self._device = VirtualDevice()
        # 一台 VID/PID 不匹配、也从不回应的干扰设备：用来看过滤和探测的效果。
        self._decoy = VirtualDevice.other_adapter()
        self._profile = self._build_profile(self._device.vid_pid)
        self._handler = SerialHandler(self._profile)
        # 只注册本示例要用的两条命令；声明来自虚拟设备的指令集。
        self._handler.register(VERSION, HELLO)
        self._results: list[CommandResult] = []
        self._attempts: list[str] = []
        self._handler.command_finished.connect(self._results.append)

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
        with self._device.use(self._decoy):
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
        """跑一次阻塞扫描并打印探测过程与结果。"""
        print(f"\n=== 阻塞扫描：{title} ===")
        self._attempts.clear()
        finder = DeviceFinder(profile)
        # probe_progress 是跨线程信号：探测在工作线程里跑，信号会排到主线程，
        # 所以 find() 阻塞期间收不到；扫描结束后泵一下事件再统一打印。
        finder.probe_progress.connect(self._on_probe_progress)
        found = finder.find(mode)
        self._pump(0.2)
        for attempt in self._attempts:
            print(f"  探测过 {attempt}")
        print(f"  找到 {[item.port for item in found]}")
        return found

    def _on_probe_progress(self, port: str, baudrate: int) -> None:
        """每尝试一对“端口 + 波特率”就回调一次，可以拿来做进度条。"""
        self._attempts.append(f"{port}@{baudrate}")

    def _connect_and_hello(self, target: str | DeviceInfo | None) -> None:
        """连接指定目标、发一条命令、断开。"""
        self._results.clear()
        print(f"\n=== 连接入口：{self._describe(target)} ===")
        try:
            # 阻塞式连接：返回时设备已打开并初始化完成，可以立刻 send()。
            self._handler.connect(target)
            print(f"  已连接，状态={self._handler.state.value}")
            self._handler.send(HELLO)
            result = self._wait_result(HELLO)
            print(f"  收到: {result.data}  原始帧: {result.raw_frames[0]!r}")
        except SerialForgeError as error:
            # 没找到设备、端口被占用、打开失败都在这里同步抛出。
            print(f"  连接失败: {error}")
        finally:
            # 同一个 handler 可以反复 connect/disconnect；不先断开不能再连。
            self._handler.disconnect()

    def _describe(self, target: str | DeviceInfo | None) -> str:
        """把三种 connect 目标写成一句人话，方便对照输出。"""
        if target is None:
            return "无参，自动筛选并按探测指令确认设备"
        if isinstance(target, DeviceInfo):
            return f"DeviceInfo({target.port}@{target.baudrate})，跳过重新探测"
        return f"端口名 {target}，绕过 VID/PID 过滤"

    def _wait_result(self, spec: CommandSpec) -> CommandResult:
        """等到某个声明的终态结果（结果由 I/O 线程发出，需要泵事件）。"""
        self._wait_until(
            lambda: any(item.spec is spec for item in self._results)
        )
        return next(item for item in self._results if item.spec is spec)

    def _pump(self, seconds: float) -> None:
        """泵一会儿 Qt 事件，让排队的跨线程信号送达。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._application.processEvents()
            time.sleep(0.005)

    def _wait_until(
        self,
        predicate: Callable[[], bool],
        timeout_s: float = 5.0,
    ) -> None:
        """泵事件直到条件成立（连接本身是阻塞的，这里只用于等命令结果）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._application.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise TimeoutError("等待超时：没有在预期时间内拿到结果")


if __name__ == "__main__":
    raise SystemExit(AutoConnectDemo().run())
