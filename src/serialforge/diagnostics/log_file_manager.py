"""Manage queued, per-connection log files."""

from __future__ import annotations

import re
from _thread import RLock as RLockType
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, TextIO

from loguru import logger

# PySide6 exposes these C++ types through generated bindings, which pylint
# cannot resolve even though ty and Python imports can.
# pylint: disable=no-name-in-module
from PySide6.QtCore import QCoreApplication, QStandardPaths

# pylint: enable=no-name-in-module
from serialforge.models import LogConfig

if TYPE_CHECKING:
    from loguru import Message, Record

_LOG_FILE_PATTERN: re.Pattern[str] = re.compile(
    r"^\d{8}_\d{6}_\d{3}(?:_\d+)?\.txt$"
)


# pylint: disable=protected-access,too-many-instance-attributes
class LogFileManager:
    """Manage one timestamped UTF-8 log file for an active connection."""

    class _QueuedFileSink:
        """Write queued loguru messages and enforce the traffic size limit."""

        def __init__(
            self,
            manager: LogFileManager,
            path: Path,
            max_file_bytes: int,
            on_error: Callable[[OSError], None],
        ) -> None:
            """Open a log file for append."""
            self._manager: LogFileManager = manager
            self._max_file_bytes: int = max_file_bytes
            self._on_error: Callable[[OSError], None] = on_error
            self._file: TextIO = path.open("a", encoding="utf-8", newline="")
            self._failed: bool = False
            self._lock: RLockType = RLock()

        def write(self, message: Message) -> None:
            """Write a formatted message unless traffic exceeds the limit."""
            with self._lock:
                if self._failed or self._file.closed:
                    return
                kind = message.record["extra"].get("traffic_kind", "")
                direction = kind if isinstance(kind, str) else ""
                byte_count = len(message.encode("utf-8"))
                manager = self._manager
                if direction in {"TX", "RX"} and (
                    manager._traffic_limit_reached
                    or manager._written_bytes + byte_count
                    > self._max_file_bytes
                ):
                    manager._dropped_rows += 1
                    if not manager._limit_notice_written:
                        manager._traffic_limit_reached = True
                        manager._limit_notice_written = True
                        self.write_event(
                            "max_file_bytes reached; "
                            "TX/RX traffic rows disabled"
                        )
                    return
                self._write(message, byte_count)

        def stop(self) -> None:
            """Close the file after loguru drains the queued messages."""
            with self._lock:
                if not self._file.closed:
                    self._file.close()

        def write_event(self, text: str) -> None:
            """Append an event row that is allowed beyond the traffic limit."""
            line = _event_line(text)
            self._write(line, len(line.encode("utf-8")))

        def _write(self, message: str, byte_count: int) -> None:
            """Write and flush one row, reporting file I/O failures."""
            try:
                self._file.write(message)
                self._file.flush()
                self._manager._written_bytes += byte_count
            except OSError as exc:
                self._failed = True
                self._file.close()
                self._on_error(exc)

    def __init__(
        self,
        config: LogConfig,
        *,
        on_error: Callable[[OSError], None] | None = None,
    ) -> None:
        """Store file policy without creating directories or files."""
        self._config: LogConfig = config
        self._on_error: Callable[[OSError], None] = (
            on_error or self._ignore_error
        )
        self._enabled: bool = config.enabled
        self._save_to_file: bool = config.save_to_file
        self._connection_id: str | None = None
        self._path: Path | None = None
        self._sink_id: int | None = None
        self._sink: LogFileManager._QueuedFileSink | None = None
        self._written_bytes: int = 0
        self._dropped_rows: int = 0
        self._traffic_limit_reached: bool = False
        self._limit_notice_written: bool = False
        self._application: QCoreApplication | None = None
        self._closed: bool = False
        self._lock: RLockType = RLock()
        application = QCoreApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.close)
            self._application = application

    @property
    def current_path(self) -> Path | None:
        """Return the current connection's file path, if one was created."""
        with self._lock:
            return self._path

    @property
    def dropped_rows(self) -> int:
        """Return the number of traffic rows omitted by the file limit."""
        with self._lock:
            return self._dropped_rows

    def set_config(self, config: LogConfig) -> None:
        """Apply runtime policy without splitting an existing file session."""
        with self._lock:
            self._config = config
            self._enabled = config.enabled
            self._save_to_file = config.save_to_file
            if self._enabled and self._save_to_file:
                self._start_sink_if_enabled()
            else:
                self._stop_sink()

    def begin_connection(self, connection_id: str) -> Path | None:
        """Start a connection session without creating a file during probing."""
        if not connection_id:
            raise ValueError("connection_id must not be empty")
        with self._lock:
            if self._closed:
                raise RuntimeError("LogFileManager is closed")
            if self._connection_id is not None:
                raise RuntimeError("a log connection is already active")
            self._connection_id = connection_id
            self._path = None
            self._written_bytes = 0
            self._dropped_rows = 0
            self._traffic_limit_reached = False
            self._limit_notice_written = False
            self._start_sink_if_enabled()
            return self._path

    def end_connection(self) -> None:
        """Finish the active connection and flush its queued file messages."""
        with self._lock:
            self._stop_sink()
            self._write_drop_summary()
            self._connection_id = None
            self._path = None

    def set_enabled(self, enabled: bool) -> None:
        """Change the total logging switch while preserving the active file."""
        with self._lock:
            self._enabled = enabled
            if enabled:
                self._start_sink_if_enabled()
            else:
                self._stop_sink()

    def set_save_to_file(self, enabled: bool) -> None:
        """Change file logging for the active connection at runtime."""
        with self._lock:
            self._save_to_file = enabled
            if enabled:
                self._start_sink_if_enabled()
            else:
                self._stop_sink()

    def close(self) -> None:
        """Flush and close the active sink during shutdown."""
        with self._lock:
            if self._closed:
                return
            self._stop_sink()
            self._write_drop_summary()
            self._connection_id = None
            self._path = None
            self._closed = True
            application = self._application
            if (
                application is not None
                and application is QCoreApplication.instance()
            ):
                application.aboutToQuit.disconnect(self.close)
            self._application = None

    def _start_sink_if_enabled(self) -> None:
        """Create or reopen the active file when both switches are on."""
        if (
            not self._enabled
            or not self._save_to_file
            or self._connection_id is None
            or self._sink_id is not None
            or self._closed
        ):
            return
        try:
            if self._path is None:
                self._path = self._create_file()
                self._written_bytes = self._path.stat().st_size
            sink = self._QueuedFileSink(
                self,
                self._path,
                self._config.max_file_bytes,
                self._handle_sink_error,
            )
            connection_id = self._connection_id

            def connection_filter(record: Record) -> bool:
                value = record["extra"].get("conn_id")
                return isinstance(value, str) and value == connection_id

            self._sink = sink
            self._sink_id = logger.add(
                sink,
                level="DEBUG",
                format="{message}",
                filter=connection_filter,
                enqueue=True,
                catch=True,
            )
        except OSError as exc:
            self._save_to_file = False
            self._on_error(exc)

    def _stop_sink(self) -> None:
        """Remove the current handler and drain queued messages."""
        if self._sink_id is None:
            return
        sink_id = self._sink_id
        self._sink_id = None
        self._sink = None
        logger.remove(sink_id)
        logger.complete()

    def _create_file(self) -> Path:
        """Create a unique timestamp path and apply safe retention cleanup."""
        directory = self._log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        base = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        suffix = 0
        while True:
            extra = f"_{suffix}" if suffix else ""
            path = directory / f"{base}{extra}.txt"
            try:
                with path.open("x", encoding="utf-8", newline=""):
                    pass
                break
            except FileExistsError:
                suffix += 1
        self._clean_old_files(directory, path)
        return path

    def _log_directory(self) -> Path:
        """Resolve an explicit folder or the app-local serial log folder."""
        if self._config.dir is not None:
            return self._config.dir
        app_name = self._config.app_name
        if app_name is None:
            app_name = QCoreApplication.applicationName() or "serialforge"
        root = Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppLocalDataLocation
            )
        )
        return root / app_name / "serial_logs"

    def _clean_old_files(self, directory: Path, current_path: Path) -> None:
        """Delete oldest matching files, leaving unrelated files untouched."""
        files = sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and _LOG_FILE_PATTERN.fullmatch(path.name)
        )
        remaining = len(files)
        for path in files:
            if remaining <= self._config.max_files:
                break
            if path == current_path:
                continue
            try:
                path.unlink()
            except FileNotFoundError:
                remaining -= 1
            except OSError:
                continue
            else:
                remaining -= 1

    def _write_drop_summary(self) -> None:
        """Append one cumulative traffic-drop summary at connection end."""
        if self._path is None or self._dropped_rows <= 0:
            return
        line = _event_line(
            f"log closed; dropped {self._dropped_rows} traffic rows"
        )
        try:
            with self._path.open("a", encoding="utf-8", newline="") as file:
                file.write(line)
                file.flush()
            self._written_bytes += len(line.encode("utf-8"))
        except OSError as exc:
            self._save_to_file = False
            self._on_error(exc)

    def _handle_sink_error(self, error: OSError) -> None:
        """Disable future file output after an asynchronous write failure."""
        self._save_to_file = False
        self._on_error(error)

    @staticmethod
    def _ignore_error(error: OSError) -> None:
        """Discard errors when no callback was supplied."""
        del error


def _event_line(text: str) -> str:
    """Format a lifecycle line for file sink quota notices and summaries."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    data = text.encode("utf-8")
    hex_text = " ".join(f"{byte:02X}" for byte in data)
    return f"{now} | EVT | - | - | {hex_text} | {text}\n"


__all__ = ["LogFileManager"]
