"""Capture bounded traffic history and emit DEBUG loguru records."""

from __future__ import annotations

from _thread import RLock as RLockType
from codecs import lookup
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import RLock
from time import monotonic, time
from uuid import uuid4

from loguru import logger

# PySide6 exposes these C++ types through generated bindings, which pylint
# cannot resolve even though ty and Python imports can.
# pylint: disable=no-name-in-module
from PySide6.QtCore import QObject, Signal

# pylint: enable=no-name-in-module
from serialforge.advanced import RuntimeConfig, TrafficRecord
from serialforge.diagnostics.log_file_manager import LogFileManager
from serialforge.enums import SendRoute
from serialforge.errors import ConfigError
from serialforge.models import CommandSpec, EventSpec, LogConfig

_TEXT_ESCAPE_TABLE: dict[int, str] = str.maketrans(
    {"\\": "\\\\", "\r": "\\r", "\n": "\\n", "|": "\\x7C"}
)


# pylint: disable=too-many-instance-attributes
class TrafficLogger(QObject):
    """Record TX/RX traffic and lifecycle events for one device handler.

    Note:
        Gate emission per instance instead of disabling the ``serialforge``
        loguru namespace during import, which would mutate host-global state.
    """

    traffic_logged = Signal(object)
    error_occurred = Signal(object)

    def __init__(
        self,
        config: LogConfig | None = None,
        runtime: RuntimeConfig | None = None,
        *,
        encoding: str = "utf-8",
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """Create an in-memory logger without changing global loguru state."""
        super().__init__()
        self._config: LogConfig = config or LogConfig()
        self._runtime: RuntimeConfig = runtime or RuntimeConfig()
        self._records: deque[TrafficRecord] = deque(
            maxlen=self._runtime.traffic_ring_size
        )
        self._records_lock: RLockType = RLock()
        self._clock: Callable[[], float] = clock
        self._pending_stream_records: dict[CommandSpec, TrafficRecord] = {}
        self._last_stream_emit_at: float = (
            clock() - self._runtime.stream_emit_interval_ms / 1000
        )
        self._enabled: bool = self._config.enabled
        try:
            lookup(encoding)
        except LookupError as exc:
            raise ConfigError(f"unknown encoding: {encoding}") from exc
        self._encoding: str = encoding
        self._connection_id: str | None = None
        self._scope_id: str = uuid4().hex
        self._port: str = "-"
        self._manager: LogFileManager = LogFileManager(
            self._config,
            on_error=self.error_occurred.emit,
        )

    @property
    def recent_records(self) -> tuple[TrafficRecord, ...]:
        """Return an immutable snapshot of the bounded TX/RX history."""
        with self._records_lock:
            return tuple(self._records)

    @property
    def current_log_path(self) -> Path | None:
        """Return the current connection's file path when file logging began."""
        return self._manager.current_path

    @property
    def dropped_traffic_lines(self) -> int:
        """Return the file sink's cumulative dropped traffic rows."""
        return self._manager.dropped_rows

    def set_log_config(self, config: LogConfig) -> None:
        """Apply a complete runtime log policy to this traffic session."""
        self._config = config
        self._enabled = config.enabled
        self._manager.set_config(config)

    def begin_connection(self, port: str) -> None:
        """Begin a log session after a connection has reached CONNECTED."""
        if self._connection_id is not None:
            raise RuntimeError("a traffic log connection is already active")
        connection_id = uuid4().hex
        self._manager.begin_connection(connection_id)
        self._connection_id = connection_id
        self._port = port
        self.record_event(port, "CONNECTED")

    def end_connection(self, port: str | None = None) -> None:
        """Write a disconnect event and flush the active connection file."""
        if self._connection_id is None:
            return
        self.record_event(port or self._port, "DISCONNECTED")
        self._manager.end_connection()
        self._connection_id = None
        self._port = "-"

    def set_enabled(self, enabled: bool) -> None:
        """Enable or silence loguru output and any configured file sink."""
        self._enabled = enabled
        self._manager.set_enabled(enabled)

    def set_save_to_file(self, enabled: bool) -> None:
        """Enable or stop file output without changing the active file path."""
        self._manager.set_save_to_file(enabled)

    def record_tx(
        self,
        port: str,
        data: bytes,
        route: SendRoute,
        spec: CommandSpec | EventSpec | None = None,
    ) -> TrafficRecord:
        """Record one transmitted frame and its command match annotation."""
        if route is SendRoute.RAW:
            annotation = "RAW"
        elif spec is None or spec.name is None:
            raise ValueError(f"route {route.value} requires a named spec")
        else:
            annotation = f"{route.name}:{spec.name}"
        record = TrafficRecord(
            direction="TX",
            timestamp=time(),
            port=port,
            data=bytes(data),
            route=route,
            spec=spec,
        )
        self._record(record, annotation)
        return record

    def record_rx(
        self,
        port: str,
        data: bytes,
        *,
        stream_spec: CommandSpec | None = None,
    ) -> TrafficRecord:
        """Record a frame, coalescing high-rate stream UI notifications."""
        record = TrafficRecord(
            direction="RX",
            timestamp=time(),
            port=port,
            data=bytes(data),
            spec=stream_spec,
        )
        self._record(record, "-", stream_spec=stream_spec)
        return record

    def flush_stream_traffic(self, *, force: bool = False) -> None:
        """Emit the latest buffered record per stream at the interval."""
        now = self._clock()
        with self._records_lock:
            if not force and now - self._last_stream_emit_at < (
                self._runtime.stream_emit_interval_ms / 1000
            ):
                return
            records = tuple(self._pending_stream_records.values())
            self._pending_stream_records.clear()
            self._last_stream_emit_at = now
        for record in records:
            self.traffic_logged.emit(record)

    def record_event(self, port: str, event: str) -> None:
        """Record one lifecycle event without adding it to the traffic ring."""
        if not self._manager_enabled:
            return
        timestamp = time()
        data = event.encode("utf-8")
        logger.bind(
            conn_id=self._connection_id or self._scope_id,
            route="event",
            spec_name="",
            traffic_kind="EVT",
        ).opt(lazy=True).debug(
            "{}",
            lambda: self._format_line(timestamp, "EVT", port, "-", data),
        )

    def close(self) -> None:
        """Flush the manager and release its application shutdown hook."""
        self.flush_stream_traffic(force=True)
        self.end_connection()
        self._manager.close()

    @property
    def _manager_enabled(self) -> bool:
        """Return whether this instance is allowed to emit loguru records."""
        return self._enabled

    def _record(
        self,
        record: TrafficRecord,
        annotation: str,
        *,
        stream_spec: CommandSpec | None = None,
    ) -> None:
        """Append, signal, and optionally log one traffic record."""
        with self._records_lock:
            self._records.append(record)
            if stream_spec is not None:
                self._pending_stream_records[stream_spec] = record
        if stream_spec is None:
            self.traffic_logged.emit(record)
        else:
            self.flush_stream_traffic()
        if not self._manager_enabled:
            return
        spec_name = self._spec_name(record.spec)
        logger.bind(
            conn_id=self._connection_id or self._scope_id,
            route=record.route.value if record.route is not None else "",
            spec_name=spec_name,
            traffic_kind=record.direction,
        ).opt(lazy=True).debug(
            "{}", lambda: self._format_traffic(record, annotation)
        )

    def _format_traffic(self, record: TrafficRecord, annotation: str) -> str:
        """Format a traffic record using the public file and console layout."""
        return self._format_line(
            record.timestamp,
            record.direction,
            record.port,
            annotation,
            record.data,
        )

    def _format_line(
        self,
        timestamp: float,
        direction: str,
        port: str,
        annotation: str,
        data: bytes,
    ) -> str:
        """Render a timestamped row with bounded hexadecimal output."""
        time_text = datetime.fromtimestamp(timestamp).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3]
        byte_limit = self._runtime.hex_truncate_bytes
        display = data[:byte_limit]
        hex_text = " ".join(f"{value:02X}" for value in display)
        if len(data) > byte_limit:
            hex_text += f" ... (+{len(data) - byte_limit} bytes)"
        text = data.decode(self._encoding, errors="replace").translate(
            _TEXT_ESCAPE_TABLE
        )
        return (
            f"{time_text} | {direction} | {port} | {annotation} | "
            f"{hex_text} | {text}"
        )

    @staticmethod
    def _spec_name(spec: object | None) -> str:
        """Return a stable display name for the bound loguru record."""
        if isinstance(spec, (CommandSpec, EventSpec)):
            return spec.name or ""
        return ""


__all__ = ["TrafficLogger"]
