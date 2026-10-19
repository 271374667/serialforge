"""共用的虚拟串口设备：一台会说人话的仪器，examples/ 下的示例都通过它取数据。

它由两部分组成：

- **设备引擎**：指令集与行为状态机，只认「请求文本 → 应答文本」，不认识串口、
  Qt 或测试替身；以后换成真 COM 口（端口对的一端）时复用同一份引擎。
- **内存挂载**：把引擎接到 ``tests/support`` 的假后端上，让库里的扫描、连接、
  收发与超时都按真实串口跑，CI 无硬件也能跑。

指令集（这台仪器能听懂的全部内容）：

| 指令 | 请求 | 应答 | 形态 | 声明 |
| --- | --- | --- | --- | --- |
| 版本查询 | `Version` | `Software version 1.02` | SINGLE | `VERSION` |
| 握手 | `Hello` | `HELLO serialforge` | SINGLE | `HELLO` |
| 批量读取 | `ReadAll` | `VALUE 1`…`VALUE 3` | MULTI，`count=3` | `READ_ALL` |
| 导出样本 | `Dump` | `ROW 1`… 再 `END` | MULTI，`until` | `DUMP` |
| 开始采集 | `Monitor` | `ADC 1` 起持续吐帧 | STREAM | `MONITOR` |
| 停止采集 | `Stop` | 无 | NO_REPLY | `STOP` |
| 上报开关 | `Report {period_ms}` | 无 | NO_REPLY | `REPORT` |
| 增益设置 | `SetGain {value}` | `GAIN 7` / `ERROR …` | SINGLE | `SET_GAIN` |
| 状态查询 | `Status` | `STATUS idle` | SINGLE，TAGGED | `STATUS` |
| 心跳 | `Ping` | `Pong` | 故意不注册 | —（演示未识别帧） |
| 周期上报 | （设备自己发） | `TEMP 25` | 主动上报 | `TELEMETRY` |
| 超温报警 | （设备自己发） | `ALARM 42` | 主动上报 | `ALARM` |

设备行为：

- 所有应答都以 ``\\r\\n`` 结尾：默认的 LINE 定界按它切帧。
- ``Monitor`` 之后设备每 150 ms 吐一条 ``ADC n``；收到任何下一条请求都先停流，
  再照常处理这条请求。流式调用自身的存活时间由 ``send_async(timeout_s=...)``
  决定（默认只有 3 秒），示例要显式给足期限。
- ``SetGain`` 超出 1..8 时回 ``ERROR out of range``，声明里的 ``error_pattern``
  把它变成设备错误（``CommandStatus.DEVICE_ERROR``）。
- 流式未结束时，库会把新的 EXCLUSIVE 命令判为 BUSY；``Stop``（NO_REPLY）与
  ``Status``（``Correlation.TAGGED``）声明成能在流里发出去，用来打断采集。
- ``Report 100`` 让设备每 100 ms **主动**推一条 ``TEMP``；温度按 22…28 循环，
  到 27 度及以上补一条 ``ALARM 42``（降回来才允许再次报警）。``Report 0`` 关闭。
- ``Dump`` 的行数由设备内部样本数决定（每采集一帧加一），所以只能用 ``until``。

要点：

- 表里的 ``Spec`` 就是使用方为自己的设备写的声明，这里集中放一份，
  示例里就只剩 serialforge 的调用。
- 使用方只接触「端口名 + 指令 + 返回值」：主动上报由引擎自己的定时器产生，
  没有任何手工注入接口。

用法::

    device = VirtualDevice()
    with device.attached():          # 把设备接到内存后端上
        ...  connect / add / send / 收信号  ...
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from serialforge import Correlation, ResponseMode, Spec
from serialforge.protocols import TransportProtocol
from tests.support import (
    FakeBackend,
    FakeTransport,
    SimulatedDevice,
    use_fake_backend,
)

# 设备的 USB 身份与工作波特率：示例把它们直接交给 scan_async。
VID_PID: Final = ((0x067B, 0x23A3),)
BAUD_RATE: Final = 115200

# 干扰设备的身份（CH340）：VID/PID 不匹配，用来演示扫描过滤。
OTHER_VID_PID: Final = ((0x1A86, 0x7523),)

# 指令集里的文本，方便示例断言和文档引用。
VERSION_TEXT: Final = "Software version 1.02"
HELLO_NAME: Final = "serialforge"
ALARM_CODE: Final = "42"
READ_ALL_COUNT: Final = 3
GAIN_MAX: Final = 8
SAMPLE_SEED: Final = 2
TERMINATOR: Final = b"\r\n"

# 设备的时间与温度模型：示例可引用，测试可断言。
STREAM_INTERVAL_S: Final = 0.15
REPORT_FAST_MS: Final = 100
REPORT_MIN_MS: Final = 20
REPORT_MAX_MS: Final = 5000
POLL_INTERVAL_S: Final = 0.005
BASE_TEMPERATURE_C: Final = 22
TEMPERATURE_CYCLE: Final = 7
ALARM_THRESHOLD_C: Final = 27


@dataclass(frozen=True)
class VersionInfo:
    """版本查询的解析结果。"""

    version: str


@dataclass(frozen=True)
class Gain:
    """增益设置的解析结果。"""

    gain: int


@dataclass(frozen=True)
class Status:
    """状态查询的解析结果。"""

    state: str


@dataclass(frozen=True)
class Telemetry:
    """周期上报的解析结果。"""

    celsius: int


@dataclass(frozen=True)
class Alarm:
    """超温报警的解析结果。"""

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
HELLO = Spec("Hello", r"HELLO (?P<name>\w+)", dict)
READ_ALL = Spec(
    "ReadAll",
    r"VALUE (?P<value>\d+)",
    mode=ResponseMode.MULTI,
    count=READ_ALL_COUNT,
    result_type=dict,
)
DUMP = Spec(
    "Dump",
    r"ROW (?P<row>\d+)",
    mode=ResponseMode.MULTI,
    until="END",
    result_type=dict,
)
MONITOR = Spec(
    "Monitor",
    r"ADC (?P<value>\d+)",
    result_type=dict,
    mode=ResponseMode.STREAM,
)
STOP = Spec("Stop", mode=ResponseMode.NO_REPLY)
REPORT = Spec(
    "Report {period_ms}",
    mode=ResponseMode.NO_REPLY,
    name="Report",
)
SET_GAIN = Spec(
    "SetGain {value}",
    r"GAIN (?P<gain>\d+)",
    Gain,
    error_pattern=r"ERROR .*",
    name="SetGain",
)
STATUS = Spec(
    "Status",
    r"STATUS (?P<state>\w+)",
    Status,
    # TAGGED：采集期间也发得出去，所以它可以用来打断流（默认 EXCLUSIVE 会被
    # 判 BUSY，见模块顶部说明）。
    correlation=Correlation.TAGGED,
)
TELEMETRY = Spec(pattern=r"TEMP (?P<celsius>-?\d+)", result_type=Telemetry)
ALARM = Spec(pattern=r"ALARM (?P<code>\d+)", result_type=Alarm)


def _frame(text: str) -> bytes:
    """把一行文本封成设备帧（应答一律以 CRLF 结尾）。"""
    return text.encode("ascii", errors="replace") + TERMINATOR


def _optional_int(text: str) -> int | None:
    """解析十进制整数参数；不是整数时返回 None。"""
    try:
        return int(text)
    except ValueError:
        return None


class _DeviceEngine:
    """设备行为状态机：只认识请求文本与应答文本。"""

    def __init__(self) -> None:
        """从空闲状态开始：不采集、不上报、没有超温。"""
        self._is_streaming: bool = False
        self._is_overheated: bool = False
        self._stream_index: int = 0
        self._samples: int = SAMPLE_SEED
        self._report_index: int = 0
        self._report_period_s: float = 0.0
        self._next_stream_at: float = 0.0
        self._next_report_at: float = 0.0

    @property
    def state(self) -> str:
        """返回设备状态名，供 ``Status`` 应答使用。"""
        if self._is_streaming:
            return "streaming"
        if self._report_period_s > 0:
            return "reporting"
        return "idle"

    def request(self, line: str, now: float) -> list[bytes]:
        """处理一条完整请求。

        Args:
            line: 去掉定界符后的一行请求文本。
            now: 单调时钟读数，供定时任务排期使用。

        Returns:
            需要立刻回给主机的帧；不需要应答的指令返回空列表。
        """
        # 任何请求都会让正在跑的采集停下来，这就是“发下一条才停”。
        self._is_streaming = False
        parts = line.strip().split(maxsplit=1)
        name = parts[0] if parts else ""
        argument = parts[1] if len(parts) > 1 else ""
        match name:
            case "Version":
                return [_frame(VERSION_TEXT)]
            case "Hello":
                return [_frame(f"HELLO {HELLO_NAME}")]
            case "ReadAll":
                return [
                    _frame(f"VALUE {index}")
                    for index in range(1, READ_ALL_COUNT + 1)
                ]
            case "Dump":
                return self._dump()
            case "Monitor":
                self._start_streaming(now)
                return []
            case "Stop":
                return []
            case "Status":
                return [_frame(f"STATUS {self.state}")]
            case "Report":
                self._set_report(argument)
                return []
            case "SetGain":
                return self._set_gain(argument)
            case "Ping":
                return [_frame("Pong")]
            case _:
                return []

    def tick(self, now: float) -> list[bytes]:
        """推进设备的定时任务：采集吐帧与周期上报。

        Args:
            now: 单调时钟读数。

        Returns:
            本次到期的帧，没有到期任务时返回空列表。
        """
        frames: list[bytes] = []
        if self._is_streaming and now >= self._next_stream_at:
            self._stream_index += 1
            self._samples += 1
            frames.append(_frame(f"ADC {self._stream_index}"))
            self._next_stream_at = now + STREAM_INTERVAL_S
        if self._report_period_s > 0 and now >= self._next_report_at:
            frames.extend(self._report(now))
        return frames

    def _start_streaming(self, now: float) -> None:
        """进入采集状态，并让第一帧立刻到期。"""
        self._is_streaming = True
        self._stream_index = 0
        self._next_stream_at = now

    def _dump(self) -> list[bytes]:
        """导出内部样本：行数由设备状态决定，所以只能用 until 收尾。"""
        rows = [_frame(f"ROW {index}") for index in range(1, self._samples + 1)]
        return [*rows, _frame("END")]

    def _set_report(self, argument: str) -> None:
        """按毫秒周期开关周期上报；0 或非法值表示关闭。"""
        period_ms = _optional_int(argument)
        if period_ms is None or period_ms <= 0:
            self._report_period_s = 0.0
            return
        clamped = min(max(period_ms, REPORT_MIN_MS), REPORT_MAX_MS)
        self._report_period_s = clamped / 1000
        self._next_report_at = 0.0

    def _set_gain(self, argument: str) -> list[bytes]:
        """设置增益：1..8 合法，其余回设备错误。"""
        gain = _optional_int(argument)
        if gain is None or not 1 <= gain <= GAIN_MAX:
            return [_frame("ERROR out of range")]
        return [_frame(f"GAIN {gain}")]

    def _report(self, now: float) -> list[bytes]:
        """产生一帧温度上报，越过阈值时再补一帧报警。

        Note:
            报警只在“刚越线”的那一帧发一次，降回阈值以下才重新武装，
            这样示例里的报警条数与温度循环一一对应，可断言。
        """
        celsius = BASE_TEMPERATURE_C + self._report_index % TEMPERATURE_CYCLE
        self._report_index += 1
        frames = [_frame(f"TEMP {celsius}")]
        if celsius >= ALARM_THRESHOLD_C:
            if not self._is_overheated:
                self._is_overheated = True
                frames.append(_frame(f"ALARM {ALARM_CODE}"))
        else:
            self._is_overheated = False
        self._next_report_at = now + self._report_period_s
        return frames


class _TrackedBackend(FakeBackend):
    """记住每个端口开出来的传输，让设备只往自己的连接注入帧。"""

    def __init__(self, devices: Sequence[SimulatedDevice]) -> None:
        """建立后端，并准备端口到传输的对应表。"""
        super().__init__(devices)
        self._opened: dict[str, list[FakeTransport]] = {}

    # ty: ignore[missing-override-decorator] -- Python 3.11 compatibility.
    def open(
        self, port: str, params: Mapping[str, object]
    ) -> TransportProtocol:
        """打开端口，并记下这只传输属于哪个端口。"""
        transport = cast(FakeTransport, super().open(port, params))
        self._opened.setdefault(port, []).append(transport)
        return transport

    def transports_of(
        self, device: SimulatedDevice
    ) -> tuple[FakeTransport, ...]:
        """返回某个设备端口上开过的全部传输（含已关闭的）。"""
        return tuple(self._opened.get(device.port, ()))


class _DeviceWorker(threading.Thread):
    """把引擎接到内存后端上：收请求、推进定时器、把产出的帧注入连接。"""

    def __init__(
        self,
        engine: _DeviceEngine,
        backend: _TrackedBackend,
        descriptor: SimulatedDevice,
    ) -> None:
        """绑定一台设备的引擎与它的传输来源。

        Args:
            engine: 负责指令与定时任务的状态机。
            backend: 能查端口对应传输的假后端。
            descriptor: 本设备在后端里的描述对象。
        """
        super().__init__(name="virtual-device", daemon=True)
        self._engine = engine
        self._backend = backend
        self._descriptor = descriptor
        self._inbox: deque[bytes] = deque()
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    def submit(self, data: bytes) -> None:
        """接收主机写出的原始字节（由 I/O 线程调用）。"""
        with self._lock:
            self._inbox.append(bytes(data))

    # ty: ignore[missing-override-decorator] -- Python 3.11 compatibility.
    def run(self) -> None:
        """按固定节拍跑设备：处理请求、推进定时器、注入产出的帧。"""
        while not self._stopped.is_set():
            now = time.monotonic()
            self._inject(self._advance(now))
            self._stopped.wait(POLL_INTERVAL_S)

    def stop(self) -> None:
        """停止后台节拍并等待线程退出。"""
        self._stopped.set()
        self.join(timeout=1.0)

    def _advance(self, now: float) -> list[bytes]:
        """取走待处理请求，返回本轮要注入的帧。"""
        with self._lock:
            chunks = list(self._inbox)
            self._inbox.clear()
        frames: list[bytes] = []
        for chunk in chunks:
            # 一条写就是一条命令：按 CRLF 切行足以覆盖示例发出的每一帧。
            for line in chunk.decode("ascii", errors="replace").split("\r\n"):
                if line.strip():
                    frames.extend(self._engine.request(line, now))
        frames.extend(self._engine.tick(now))
        return frames

    def _inject(self, frames: Sequence[bytes]) -> None:
        """把帧塞给本设备已打开且还活着的连接。"""
        if not frames:
            return
        for transport in self._backend.transports_of(self._descriptor):
            if transport.closed:
                continue
            for frame in frames:
                transport.inject(frame)


class VirtualDevice:
    """一台虚拟仪器：身份固定，指令集见模块顶部。"""

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
            silent: 为真时这台设备没有任何行为，既不应答也不上报。
        """
        self.port = port
        self.vid_pid = vid_pid
        self.baud_rate = baud_rate
        self._engine: _DeviceEngine | None = None if silent else _DeviceEngine()
        self._worker: _DeviceWorker | None = None

    @classmethod
    def other_adapter(cls) -> VirtualDevice:
        """造一台干扰设备：VID/PID 不匹配且从不回应（演示扫描过滤）。"""
        return cls(port="SIM-CH340", vid_pid=OTHER_VID_PID, silent=True)

    @contextmanager
    def attached(self, *extra: VirtualDevice) -> Iterator[VirtualDevice]:
        """在 ``with`` 内让库把这些虚拟设备当作真实串口使用。

        内存后端与设备的后台节拍都只在 ``with`` 内生效，退出即恢复，所以多个
        示例互不干扰。``*extra`` 中附加的设备（如干扰设备）也一起接入。

        Args:
            *extra: 需要同时接入的其他虚拟设备。

        Yields:
            这台设备本身，便于写成 ``with device.attached() as device:``。
        """
        devices = (self, *extra)
        descriptors = {item: item._descriptor() for item in devices}
        backend = _TrackedBackend(list(descriptors.values()))
        worker = (
            None
            if self._engine is None
            else _DeviceWorker(self._engine, backend, descriptors[self])
        )
        self._worker = worker
        try:
            with use_fake_backend(backend):
                if worker is not None:
                    worker.start()
                yield self
        finally:
            if worker is not None:
                worker.stop()
            self._worker = None

    def declarations(self) -> tuple[Spec[Any], ...]:
        """本设备的全部声明，便于示例逐条 ``add(...)`` 注册。"""
        return (
            VERSION,
            HELLO,
            READ_ALL,
            DUMP,
            MONITOR,
            STOP,
            REPORT,
            SET_GAIN,
            STATUS,
            TELEMETRY,
            ALARM,
        )

    def _descriptor(self) -> SimulatedDevice:
        """把本设备翻译成内存后端认识的一台模拟设备。"""
        return SimulatedDevice(
            vid=self.vid_pid[0][0],
            pid=self.vid_pid[0][1],
            port=self.port,
            baudrate=self.baud_rate,
            script=self._on_write,
        )

    def _on_write(self, data: bytes) -> None:
        """把主机写出的字节交给后台节拍（在 I/O 线程上被调用）。"""
        if self._worker is not None:
            self._worker.submit(data)
