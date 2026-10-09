"""共用的虚拟串口设备：examples/ 下的示例都通过它取数据，不碰真实串口。

它把 ``tests/support`` 的内存后端包装成一台“会说人话”的设备，这样每个示例
只需要写 serialforge 的调用（连接、注册、发送、收信号），不用各自再实现
一遍假后台，也不用各自再定义一遍指令。

指令集（这台虚拟设备能听懂的全部内容）：

| 指令 | 请求 | 应答 | 形态 | 声明 |
| --- | --- | --- | --- | --- |
| 版本查询 | `Version` | `Software version 1.02` | SINGLE | `VERSION` |
| 批量读取 | `ReadAll` | `VALUE 1`…`VALUE 3` | MULTI 三帧 | `READ_ALL` |
| 开始监控 | `Monitor` | `ADC 1` 起连续 4 帧 | STREAM | `MONITOR` |
| 停止监控 | `Stop` | 无 | NO_REPLY | `STOP` |
| 重启 | `Reboot` | 无 | NO_REPLY | `REBOOT` |
| 握手 | `Hello` | `HELLO serialforge` | SINGLE | `HELLO` |
| 心跳 | `Ping` | `Pong` | 故意不注册声明 | —（演示未识别帧） |
| 报警 | （设备自己发） | `ALARM 42` | 主动上报 | `ALARM` |

所有文本应答都以 ``\\r\\n`` 结尾：默认的 LINE 定界按它切帧。

要点：

- 表里的 统一的 ``Spec`` 就是使用方为自己的设备写的声明，
  这里集中放一份，示例里就只剩 serialforge 的调用。
- ``Ping`` 故意不注册：它的回包会作为“未识别帧”从 ``received`` 出来。
- ``ALARM`` 是设备主动发的；真机由设备自己推，这里用 ``inject_alarm()`` 注入。

用法::

    device = VirtualDevice()
    with device.use():
        ...  connect / register / send / 收信号  ...
        device.inject_alarm()      # 让设备主动上报一条报警
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from serialforge import ResponseMode, Spec
from tests.support import FakeBackend, SimulatedDevice, use_fake_backend

# 设备的 USB 身份与工作波特率：示例用它们拼 DeviceProfile。
VID_PID: Final = ((0x067B, 0x23A3),)
BAUD_RATE: Final = 115200

# 干扰设备的身份（CH340）：VID/PID 不匹配，用来演示扫描过滤。
OTHER_VID_PID: Final = ((0x1A86, 0x7523),)

# 指令集里的文本，方便示例断言和文档引用。
VERSION_TEXT: Final = "Software version 1.02"
ALARM_CODE: Final = "42"


@dataclass(frozen=True)
class VersionInfo:
    """版本查询的解析结果。"""

    version: str


@dataclass(frozen=True)
class Alarm:
    """设备主动上报的报警。"""

    code: str


# 指令集：请求文本、应答正则和结果类型都在这里声明一次。
VERSION = Spec(
    "Version",
    r"Software version (?P<version>\d+\.\d+)",
    VersionInfo,
    # 单次探测的等待上限：虚拟设备应声很快，真机要按设备实际响应速度调大，
    # 否则扫描会把时间都花在等失败的波特率上。
    timeout_s=0.3,
)
READ_ALL = Spec(
    "ReadAll",
    r"VALUE (?P<value>\d+)",
    mode=ResponseMode.MULTI,
    count=3,
    result_type=dict,
)
MONITOR = Spec(
    "Monitor",
    r"ADC (?P<value>\d+)",
    result_type=dict,
    mode=ResponseMode.STREAM,
)
STOP = Spec("Stop", mode=ResponseMode.NO_REPLY)
REBOOT = Spec("Reboot", mode=ResponseMode.NO_REPLY)
HELLO = Spec("Hello", r"HELLO (?P<name>\w+)", dict)
ALARM = Spec(pattern=r"ALARM (?P<code>\d+)", result_type=Alarm)


class VirtualDevice:
    """一台虚拟串口设备：身份固定，指令集见模块顶部。"""

    def __init__(
        self,
        port: str = "SIM-DEVICE",
        *,
        vid_pid: tuple[tuple[int, int], ...] = VID_PID,
        baud_rate: int = BAUD_RATE,
        silent: bool = False,
    ) -> None:
        """描述一台设备；``silent=True`` 表示它从不回应（当干扰设备用）。

        Args:
            port: 虚拟端口名，示例里可以按名字连接。
            vid_pid: USB 身份；扫描时用它过滤，不匹配的设备不会被探测。
            baud_rate: 设备只在这个波特率下发话，其余波特率探测会失败。
            silent: 为真时这台设备对任何请求都不回应。
        """
        self.port = port
        self.vid_pid = vid_pid
        self.baud_rate = baud_rate
        self._silent = silent
        self._backend: FakeBackend | None = None

    @classmethod
    def other_adapter(cls) -> VirtualDevice:
        """造一台干扰设备：VID/PID 不匹配且从不回应（演示扫描过滤）。"""
        return cls(
            port="SIM-CH340",
            vid_pid=OTHER_VID_PID,
            silent=True,
        )

    @contextmanager
    def use(self, *extra: VirtualDevice) -> Iterator[VirtualDevice]:
        """在 ``with`` 内让库把这些虚拟设备当作真实串口使用。

        内存后端只在 ``with`` 内生效，退出即恢复，所以多个示例互相不干扰。
        附加的设备（比如干扰设备）会一起接入同一个后端。
        """
        devices = (self, *extra)
        backend = FakeBackend([item._descriptor() for item in devices])
        for item in devices:
            item._backend = backend
        try:
            with use_fake_backend(backend):
                yield self
        finally:
            for item in devices:
                item._backend = None

    def inject_alarm(self, code: str = ALARM_CODE) -> None:
        """让设备主动上报一帧报警。

        真机上这帧由设备自己发；与请求无关的帧就是“主动上报”，会走
        ``received``。这里借内存后端的注入接口手工制造一帧。
        """
        if self._backend is None or not self._backend.transports:
            raise RuntimeError("请先进入 device.use() 并完成连接，再注入上报")
        self._backend.transports[-1].inject(f"ALARM {code}\r\n".encode())

    def declarations(self) -> tuple[Spec[Any], ...]:
        """本设备的全部声明，便于一次性 ``handler.register(*...)``。"""
        return (VERSION, READ_ALL, MONITOR, STOP, REBOOT, HELLO, ALARM)

    def _descriptor(self) -> SimulatedDevice:
        """把本设备翻译成内存后端认识的一台模拟设备。"""
        responses: dict[bytes | str, bytes | Sequence[bytes]] = (
            {} if self._silent else self._responses()
        )
        return SimulatedDevice(
            vid=self.vid_pid[0][0],
            pid=self.vid_pid[0][1],
            port=self.port,
            baudrate=self.baud_rate,
            responses=responses,
        )

    @staticmethod
    def _responses() -> dict[bytes | str, bytes | Sequence[bytes]]:
        """指令集对应的应答表，每一项都能在上面的表格里找到。"""
        return {
            b"Version\r\n": f"{VERSION_TEXT}\r\n".encode(),
            b"ReadAll\r\n": [
                b"VALUE 1\r\n",
                b"VALUE 2\r\n",
                b"VALUE 3\r\n",
            ],
            b"Monitor\r\n": [
                b"ADC 1\r\n",
                b"ADC 2\r\n",
                b"ADC 3\r\n",
                b"ADC 4\r\n",
            ],
            b"Hello\r\n": b"HELLO serialforge\r\n",
            # Ping 没有对应声明：回包会作为未识别帧上报。
            b"Ping\r\n": b"Pong\r\n",
            # Stop / Reboot 是 NO_REPLY，设备不需要答话。
        }
