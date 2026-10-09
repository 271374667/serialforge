"""Advanced configuration and diagnostics types.

The names in this module are intentionally outside the daily top-level API.
They let an application tune transport details without coupling normal code to
the implementation subpackages.
"""

from __future__ import annotations

# Lazy advanced re-exports avoid definition/implementation cycles.
# pylint: disable=import-outside-toplevel
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from serialforge.enums import (
    Checksum,
    CommandPriority,
    DataBits,
    FlowControl,
    FramingMode,
    Parity,
    SendRoute,
    StopBits,
    TimeoutPolicy,
)
from serialforge.errors import ConfigError

if TYPE_CHECKING:
    from serialforge.connection.command_call import CommandCall
    from serialforge.discovery.device_finder import DeviceFinder
    from serialforge.enums import SpecRole
    from serialforge.models import DeviceProfile, LogConfig


# Public schemas intentionally expose all transport knobs in one frozen object.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True)
class SerialConfig:
    """Store low-level port settings, defaulting to 8N1 without flow control.

    Args:
        data_bits: DataBits value; EIGHT by default.
        parity: Parity value; NONE by default.
        stop_bits: StopBits value; ONE by default.
        flow_control: FlowControl value; NONE by default.
        write_timeout_s: Positive maximum write-blocking seconds (1.0 default).
        rx_buffer_size: Positive driver receive capacity, or None to leave it.
        dtr: Explicit DTR state, or None to keep the backend default.
        rts: Explicit RTS state, or None to keep the backend default.
        rs485_switch_delay_s: Optional non-negative switching delay in seconds.
        settle_time_s: Non-negative opening wait, in seconds (0.3 default).

    Note:
        DTR/RTS settings do not promise to prevent Windows driver open pulses.

    Example:
        >>> SerialConfig().data_bits is DataBits.EIGHT
        True
    """

    data_bits: DataBits = DataBits.EIGHT
    parity: Parity = Parity.NONE
    stop_bits: StopBits = StopBits.ONE
    flow_control: FlowControl = FlowControl.NONE
    write_timeout_s: float = 1.0
    rx_buffer_size: int | None = None
    dtr: bool | None = None
    rts: bool | None = None
    rs485_switch_delay_s: float | None = None
    settle_time_s: float = 0.3

    def __post_init__(self) -> None:
        """Validate serial timing and buffer settings."""
        if self.write_timeout_s <= 0:
            raise ConfigError("write_timeout_s must be positive")
        if self.rx_buffer_size is not None and self.rx_buffer_size <= 0:
            raise ConfigError("rx_buffer_size must be positive")
        if (
            self.rs485_switch_delay_s is not None
            and self.rs485_switch_delay_s < 0
        ):
            raise ConfigError("rs485_switch_delay_s cannot be negative")
        if self.settle_time_s < 0:
            raise ConfigError("settle_time_s cannot be negative")


@dataclass(frozen=True)
class ProbeSpec:
    """Describe a custom probe when a SINGLE CommandSpec is insufficient.

    Args:
        request: Non-empty text without placeholders.
        expect: Full-match response regex or text predicate; must reject echo.
        timeout_s: Positive response timeout in seconds.
        settle_time_s: Optional non-negative override for the opening wait.
    """

    request: str
    expect: str | Callable[[str], bool]
    timeout_s: float = 1.0
    settle_time_s: float | None = None

    def __post_init__(self) -> None:
        """Validate the custom probe declaration."""
        if not self.request.strip():
            raise ConfigError("ProbeSpec.request must not be empty")
        if "{" in self.request or "}" in self.request:
            raise ConfigError("ProbeSpec.request cannot contain placeholders")
        if self.timeout_s <= 0:
            raise ConfigError("ProbeSpec.timeout_s must be positive")
        if self.settle_time_s is not None and self.settle_time_s < 0:
            raise ConfigError("ProbeSpec.settle_time_s cannot be negative")
        if isinstance(self.expect, str):
            try:
                pattern = re.compile(self.expect)
            except re.error as exc:
                raise ConfigError(f"invalid ProbeSpec.expect: {exc}") from exc
            if pattern.fullmatch(self.request.strip()):
                raise ConfigError("ProbeSpec.expect must not match its request")


# Framing options are a single public schema by design.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True)
class FramingConfig:
    """Describe receive framing independently of request termination.

    Args:
        mode: LINE, DELIMITED, LENGTH_PREFIX or SILENCE_GAP strategy.
        terminator: LINE receive delimiter (CRLF by default).
        start_marker: DELIMITED frame prefix.
        end_marker: DELIMITED frame suffix.
        length_offset: Non-negative offset of the binary length field.
        length_size: Positive byte width of the binary length field.
        byteorder: Binary length order: little or big.
        max_frame_length: Positive maximum frame size in bytes.
        max_buffer_bytes: Positive receive buffer capacity in bytes.
        silence_gap_s: Positive idle seconds; required for SILENCE_GAP.
        checksum: Checksum algorithm, NONE by default.
    """

    mode: FramingMode = FramingMode.LINE
    terminator: bytes = b"\r\n"
    start_marker: bytes = b""
    end_marker: bytes = b""
    length_offset: int = 0
    length_size: int = 2
    byteorder: str = "little"
    max_frame_length: int = 65536
    max_buffer_bytes: int = 262144
    silence_gap_s: float | None = None
    checksum: Checksum = Checksum.NONE

    def __post_init__(self) -> None:
        """Validate framing boundaries."""
        if self.mode is FramingMode.LINE and not self.terminator:
            raise ConfigError("LINE framing requires a non-empty terminator")
        if self.mode is FramingMode.SILENCE_GAP and self.silence_gap_s is None:
            raise ConfigError("SILENCE_GAP framing requires silence_gap_s")
        if self.length_offset < 0 or self.length_size <= 0:
            raise ConfigError("length_offset and length_size must be positive")
        if self.byteorder not in {"little", "big"}:
            raise ConfigError("byteorder must be 'little' or 'big'")
        if self.max_frame_length <= 0 or self.max_buffer_bytes <= 0:
            raise ConfigError("frame limits must be positive")
        if self.silence_gap_s is not None and self.silence_gap_s <= 0:
            raise ConfigError("silence_gap_s must be positive")


@dataclass(frozen=True)
class ReadLoopConfig:
    """Configure adaptive read polling, with all durations in seconds.

    Args:
        poll_min_s: Positive lower bound, below poll_initial_s (0.005 default).
        poll_max_s: Upper bound, at least poll_initial_s (0.100 default).
        poll_initial_s: Cold-start interval between the bounds (0.050 default).
        poll_factor: Positive factor relating RTT to the polling interval.
        min_samples: Positive samples required before adapting the interval.

    Note:
        Nominal intervals do not guarantee the host's timer or driver precision.
    """

    poll_min_s: float = 0.005
    poll_max_s: float = 0.100
    poll_initial_s: float = 0.050
    poll_factor: float = 0.25
    min_samples: int = 5

    def __post_init__(self) -> None:
        """Validate adaptive polling bounds."""
        if not 0 < self.poll_min_s < self.poll_initial_s <= self.poll_max_s:
            raise ConfigError(
                "poll_min_s < poll_initial_s <= poll_max_s is required"
            )
        if self.poll_factor <= 0:
            raise ConfigError("poll_factor must be positive")
        if self.min_samples <= 0:
            raise ConfigError("min_samples must be positive")


# Runtime tuning is grouped here so callers do not pass a long parameter list.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True)
class RuntimeConfig:
    """Configure queueing, probing, reconnect and backpressure behavior.

    Args:
        min_command_interval_s: Non-negative spacing between writes in seconds.
        queue_limit: Positive pending command capacity.
        post_timeout_drain_s: Late exclusive-reply isolation seconds.
        rto_min_s: Positive lower bound for AUTO response timeouts in seconds.
        rto_max_s: AUTO upper bound, at least rto_min_s.
        stream_emit_interval_ms: Positive interval for queued events, in ms.
        event_queue_high_water: Positive event queue capacity.
        traffic_ring_size: Positive in-memory traffic capacity.
        hex_truncate_bytes: Positive maximum hex-display bytes per frame.
        resend_idempotent: Retry only safe declarations after reconnect.
        queue_while_reconnecting: Accept new queued work while reconnecting.
        reconnect_initial_s: Positive initial backoff in seconds.
        reconnect_max_s: Backoff ceiling, at least reconnect_initial_s.
        reconnect_max_attempts: Positive retry limit, or None for no limit.
        reprobe_on_reconnect: Recheck the device identity after losing a handle.
        hotplug_poll_s: Positive delay between absent-device scans, in seconds.
        probe_pool_size: Positive maximum concurrently probed ports.
        probe_total_timeout_s: Positive total discovery deadline in seconds.
    """

    min_command_interval_s: float = 0.0
    queue_limit: int = 256
    post_timeout_drain_s: float = 0.2
    rto_min_s: float = 0.05
    rto_max_s: float = 5.0
    stream_emit_interval_ms: int = 50
    event_queue_high_water: int = 1000
    traffic_ring_size: int = 1000
    hex_truncate_bytes: int = 64
    resend_idempotent: bool = False
    queue_while_reconnecting: bool = False
    reconnect_initial_s: float = 0.5
    reconnect_max_s: float = 10.0
    reconnect_max_attempts: int | None = None
    reprobe_on_reconnect: bool = True
    hotplug_poll_s: float = 1.5
    probe_pool_size: int = 4
    probe_total_timeout_s: float = 30.0

    def __post_init__(self) -> None:
        """Validate runtime limits and timeout ranges."""
        if self.min_command_interval_s < 0:
            raise ConfigError("min_command_interval_s cannot be negative")
        if self.queue_limit <= 0 or self.event_queue_high_water <= 0:
            raise ConfigError("queue limits must be positive")
        if self.post_timeout_drain_s < 0:
            raise ConfigError("post_timeout_drain_s cannot be negative")
        if not 0 < self.rto_min_s <= self.rto_max_s:
            raise ConfigError("rto_min_s must be <= rto_max_s and positive")
        if self.stream_emit_interval_ms <= 0:
            raise ConfigError("stream_emit_interval_ms must be positive")
        if self.traffic_ring_size <= 0 or self.hex_truncate_bytes <= 0:
            raise ConfigError(
                "traffic_ring_size and hex_truncate_bytes must be positive"
            )
        if not 0 < self.reconnect_initial_s <= self.reconnect_max_s:
            raise ConfigError("reconnect backoff bounds are invalid")
        if (
            self.reconnect_max_attempts is not None
            and self.reconnect_max_attempts <= 0
        ):
            raise ConfigError("reconnect_max_attempts must be positive")
        if self.hotplug_poll_s <= 0 or self.probe_pool_size <= 0:
            raise ConfigError(
                "hotplug_poll_s and probe_pool_size must be positive"
            )
        if self.probe_total_timeout_s <= 0:
            raise ConfigError("probe_total_timeout_s must be positive")


@dataclass(frozen=True)
class HandlerStats:
    """Expose immutable latency estimates and drop counters.

    Attributes:
        srtt_s: Smoothed RTT seconds; None before measurements.
        rttvar_s: Smoothed RTT variation seconds; None before measurements.
        poll_interval_s: Current polling seconds, if observed.
        rto_s: Current response timeout estimate in seconds, if available.
        dropped_events: Number discarded by event backpressure.
        dropped_traffic_lines: Number discarded by the file traffic cap.
    """

    srtt_s: float | None = None
    rttvar_s: float | None = None
    poll_interval_s: float | None = None
    rto_s: float | None = None
    dropped_events: int = 0
    dropped_traffic_lines: int = 0
    dropped_background_errors: int = 0


@dataclass(frozen=True)
class TrafficRecord:
    """Describe one traffic line emitted through traffic_logged.

    Attributes:
        direction: TX, RX or EVT.
        timestamp: Wall-clock seconds since the Unix epoch.
        port: Port name for the connection.
        data: Raw traffic bytes or encoded lifecycle text.
        route: SendRoute for TX; None otherwise.
        spec: Original TX declaration; None for raw sends or receive traffic.
    """

    direction: str
    timestamp: float
    port: str
    data: bytes
    route: SendRoute | None = None
    spec: object | None = None


class CommandTicket:
    """Represent an asynchronous command and allow local cancellation."""

    request_id: str
    spec: object | None
    route: SendRoute
    context: Mapping[str, object]
    deadline: float
    _cancelled: bool

    def __init__(
        self,
        request_id: str,
        spec: object | None,
        route: SendRoute,
    ) -> None:
        """Create a ticket for a queued command.

        Args:
            request_id: Unique invocation identifier.
            spec: Original registered declaration, or None for raw text.
            route: Resolution path taken by the send entry point.
        """
        self.request_id = request_id
        self.spec = spec
        self.route = route
        self._cancelled = False
        self.deadline = float("inf")
        self.context = MappingProxyType({})

    def cancel(self) -> None:
        """Request local cancellation of the command."""
        self._cancelled = True

    def _complete(self, result: object) -> None:
        """Allow invocation handles to retain completion before notification."""

    @property
    def cancelled(self) -> bool:
        """Return whether cancellation was requested."""
        return self._cancelled


__all__ = [
    "SerialConfig",
    "DataBits",
    "Parity",
    "StopBits",
    "FlowControl",
    "ProbeSpec",
    "FramingMode",
    "FramingConfig",
    "Checksum",
    "CommandPriority",
    "CommandTicket",
    "SendRoute",
    "TimeoutPolicy",
    "TrafficRecord",
    "ReadLoopConfig",
    "RuntimeConfig",
    "HandlerStats",
]


def __getattr__(name: str) -> Any:
    """Resolve advanced facade types lazily to avoid implementation cycles."""
    if name == "CommandCall":
        from serialforge.connection.command_call import CommandCall

        return CommandCall
    if name == "SpecRole":
        from serialforge.enums import SpecRole

        return SpecRole
    if name in ("DeviceProfile", "LogConfig"):
        from serialforge.models import DeviceProfile, LogConfig

        return {"DeviceProfile": DeviceProfile, "LogConfig": LogConfig}[name]
    if name == "DeviceFinder":
        from serialforge.discovery.device_finder import DeviceFinder

        return DeviceFinder
    raise AttributeError(name)


__all__ += [
    "CommandCall",
    "SpecRole",
    "DeviceProfile",
    "LogConfig",
    "DeviceFinder",
]
