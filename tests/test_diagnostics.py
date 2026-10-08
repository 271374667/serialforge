"""M3 diagnostics and M4 stream traffic contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from serialforge.advanced import RuntimeConfig, TrafficRecord
from serialforge.diagnostics import TrafficLogger
from serialforge.diagnostics import log_file_manager as log_files
from serialforge.enums import ResponseMode, SendRoute
from serialforge.errors import ConfigError
from serialforge.models import CommandSpec, LogConfig


def test_stream_signal_coalesces_per_spec_without_losing_history() -> None:
    """Bound UI notifications while retaining each frame in the ring."""
    now = [0.0]
    traffic = TrafficLogger(
        LogConfig(),
        RuntimeConfig(stream_emit_interval_ms=50, traffic_ring_size=10),
        clock=lambda: now[0],
    )
    first = CommandSpec("MonitorA", mode=ResponseMode.STREAM)
    second = CommandSpec("MonitorB", mode=ResponseMode.STREAM)
    emitted: list[TrafficRecord] = []
    traffic.traffic_logged.connect(emitted.append)
    try:
        traffic.record_rx("COM11", b"A 1", stream_spec=first)
        now[0] = 0.01
        traffic.record_rx("COM11", b"A 2", stream_spec=first)
        traffic.record_rx("COM11", b"B 1", stream_spec=second)
        traffic.record_rx("COM11", b"A 3", stream_spec=first)
        assert len(emitted) == 1
        assert len(traffic.recent_records) == 4
        now[0] = 0.06
        traffic.flush_stream_traffic()
        assert [record.data for record in emitted] == [b"A 1", b"A 3", b"B 1"]
        assert emitted[1].spec is first
        assert emitted[2].spec is second
    finally:
        traffic.close()


def test_disabled_logger_is_silent_and_enabled_records_are_debug(
    tmp_path: Path,
) -> None:
    captured: list[Mapping[str, Any]] = []
    sink_id = logger.add(
        lambda message: captured.append(message.record), level="DEBUG"
    )
    try:
        silent = TrafficLogger(LogConfig(save_to_file=True, dir=tmp_path))
        silent.begin_connection("COM1")
        silent.record_tx("COM1", b"PING", SendRoute.RAW)
        assert captured == []
        assert list(tmp_path.glob("*.txt")) == []
        silent.end_connection()

        active = TrafficLogger(LogConfig(enabled=True))
        active.record_tx("COM1", b"PING", SendRoute.RAW)
        assert len(captured) == 1
        assert captured[0]["level"].name == "DEBUG"
        assert " | TX | COM1 | RAW | " in captured[0]["message"]
        command = CommandSpec("Version", name="get_version")
        active.record_tx("COM1", b"Version", SendRoute.SPEC, command)
        assert len(captured) == 2
        assert " | TX | COM1 | SPEC:get_version | " in captured[1]["message"]
        active.set_enabled(False)
        active.record_tx("COM1", b"QUIET", SendRoute.RAW)
        assert len(captured) == 2
        active.set_enabled(True)
        active.record_tx("COM1", b"LOUD", SendRoute.RAW)
        assert len(captured) == 3
    finally:
        logger.remove(sink_id)


def test_connection_file_has_one_annotated_row_and_bounded_ring(
    tmp_path: Path,
) -> None:
    traffic = TrafficLogger(
        LogConfig(enabled=True, save_to_file=True, dir=tmp_path),
        RuntimeConfig(traffic_ring_size=2, hex_truncate_bytes=2),
    )
    command = CommandSpec(
        "Version", pattern=r"VERSION (?P<value>.+)", name="get_version"
    )
    signaled: list[object] = []
    traffic.traffic_logged.connect(signaled.append)
    traffic.begin_connection("COM7")
    tx = traffic.record_tx("COM7", b"Version\r\n", SendRoute.MATCHED, command)
    traffic.record_rx("COM7", b"VERSION 1.0")
    traffic.record_rx("COM7", b"NEXT")
    for attempt in range(1, 4):
        traffic.record_event("COM7", f"RECONNECTED attempt={attempt}")
    path = traffic.current_log_path
    assert path is not None
    assert traffic.current_log_path == path
    assert len(list(tmp_path.glob("*.txt"))) == 1
    traffic.end_connection()

    assert tx.spec is command
    assert signaled[0] is tx
    assert len(signaled) == 3
    assert len(traffic.recent_records) == 2
    assert [record.direction for record in traffic.recent_records] == [
        "RX",
        "RX",
    ]
    assert path.name.startswith(datetime.now().strftime("%Y%m%d_%H%M"))
    contents = path.read_text(encoding="utf-8")
    tx_rows = [line for line in contents.splitlines() if " | TX | " in line]
    assert len(tx_rows) == 1
    assert " | MATCHED:get_version | " in tx_rows[0]
    assert "Version\\r\\n" in tx_rows[0]
    assert " ... (+7 bytes)" in tx_rows[0]
    assert " | RX | COM7 | - | " in contents
    assert " | EVT | COM7 | - | " in contents
    assert contents.count("RECONNECTED attempt=") == 3
    traffic.begin_connection("COM7")
    second_path = traffic.current_log_path
    assert second_path is not None
    assert second_path != path
    traffic.end_connection()
    assert len(list(tmp_path.glob("*.txt"))) == 2
    traffic.close()


def test_file_sinks_are_isolated_between_connections(tmp_path: Path) -> None:
    first = TrafficLogger(
        LogConfig(enabled=True, save_to_file=True, dir=tmp_path)
    )
    second = TrafficLogger(
        LogConfig(enabled=True, save_to_file=True, dir=tmp_path)
    )
    first.begin_connection("COM1")
    second.begin_connection("COM2")
    first_path = first.current_log_path
    second_path = second.current_log_path
    assert first_path is not None
    assert second_path is not None

    first.record_rx("COM1", b"FIRST_ONLY")
    second.record_rx("COM2", b"SECOND_ONLY")
    first.end_connection()
    second.end_connection()

    first_text = first_path.read_text(encoding="utf-8")
    second_text = second_path.read_text(encoding="utf-8")
    assert "FIRST_ONLY" in first_text
    assert "SECOND_ONLY" not in first_text
    assert "SECOND_ONLY" in second_text
    assert "FIRST_ONLY" not in second_text
    first.close()
    second.close()


def test_logger_uses_configured_text_encoding_and_validates_it() -> None:
    traffic = TrafficLogger(LogConfig(enabled=True), encoding="gbk")
    captured: list[str] = []
    sink_id = logger.add(
        lambda message: captured.append(message.record["message"]),
        level="DEBUG",
    )
    traffic.record_rx("COM9", "测试".encode("gbk"))
    logger.remove(sink_id)
    assert "测试" in captured[0]

    try:
        TrafficLogger(encoding="unknown-encoding")
    except ConfigError:
        pass
    else:
        raise AssertionError("expected ConfigError")
    traffic.close()


def test_probe_does_not_create_file_and_runtime_toggle_reuses_path(
    tmp_path: Path,
) -> None:
    traffic = TrafficLogger(
        LogConfig(enabled=True, save_to_file=True, dir=tmp_path)
    )
    traffic.record_rx("COM3", b"PROBE")
    assert list(tmp_path.glob("*.txt")) == []

    traffic.begin_connection("COM3")
    path = traffic.current_log_path
    assert path is not None
    assert path.is_file()
    traffic.set_save_to_file(False)
    traffic.record_rx("COM3", b"OFF")
    traffic.set_save_to_file(True)
    assert traffic.current_log_path == path
    traffic.record_rx("COM3", b"ON")
    traffic.end_connection()

    contents = path.read_text(encoding="utf-8")
    assert "PROBE" not in contents
    assert "OFF" not in contents
    assert "ON" in contents
    traffic.close()


def test_retention_only_removes_old_timestamp_named_logs(
    tmp_path: Path,
) -> None:
    oldest = tmp_path / "20200101_000000_000.txt"
    recent = tmp_path / "20200102_000000_000.txt"
    unrelated = tmp_path / "notes.txt"
    oldest.write_text("old", encoding="utf-8")
    recent.write_text("recent", encoding="utf-8")
    unrelated.write_text("keep", encoding="utf-8")

    traffic = TrafficLogger(
        LogConfig(
            enabled=True,
            save_to_file=True,
            dir=tmp_path,
            max_files=2,
        )
    )
    traffic.begin_connection("COM1")
    traffic.end_connection()

    assert not oldest.exists()
    assert recent.exists()
    assert unrelated.exists()
    assert len(list(tmp_path.glob("[0-9]*.txt"))) == 2
    traffic.close()


def test_timestamp_collision_adds_suffix(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FixedDateTime:
        @staticmethod
        def now() -> datetime:
            return datetime(2026, 10, 1, 10, 11, 12, 123000)

    monkeypatch.setattr(log_files, "datetime", FixedDateTime)
    paths: list[Path] = []
    for connection_id in ("first", "second"):
        manager = log_files.LogFileManager(
            LogConfig(enabled=True, save_to_file=True, dir=tmp_path)
        )
        manager.begin_connection(connection_id)
        assert manager.current_path is not None
        paths.append(manager.current_path)
        manager.end_connection()

    assert [path.name for path in paths] == [
        "20261001_101112_123.txt",
        "20261001_101112_123_1.txt",
    ]


def test_file_limit_drops_traffic_but_keeps_lifecycle_rows(
    tmp_path: Path,
) -> None:
    traffic = TrafficLogger(
        LogConfig(
            enabled=True,
            save_to_file=True,
            dir=tmp_path,
            max_file_bytes=1,
        )
    )
    traffic.begin_connection("COM8")
    traffic.record_tx("COM8", b"PING", SendRoute.RAW)
    traffic.set_save_to_file(False)
    traffic.set_save_to_file(True)
    traffic.record_rx("COM8", b"PONG")
    traffic.record_event("COM8", "RECONNECTED attempt=1")
    path = traffic.current_log_path
    assert path is not None
    traffic.end_connection()
    contents = path.read_text(encoding="utf-8")

    assert " | TX | " not in contents
    assert " | RX | " not in contents
    assert contents.count("max_file_bytes reached") == 1
    assert " | EVT | COM8 | - | " in contents
    assert "dropped 2 traffic rows" in contents
    traffic.close()


def test_unusable_directory_reports_error_without_raising(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "not_a_directory"
    file_path.write_text("x", encoding="utf-8")
    traffic = TrafficLogger(
        LogConfig(enabled=True, save_to_file=True, dir=file_path)
    )
    errors: list[object] = []
    traffic.error_occurred.connect(errors.append)

    traffic.begin_connection("COM4")

    assert len(errors) == 1
    assert isinstance(errors[0], OSError)
    assert traffic.current_log_path is None
    traffic.end_connection()
    traffic.close()


def test_retention_skips_files_that_cannot_be_deleted(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    locked = tmp_path / "20200101_000000_000.txt"
    removable = tmp_path / "20200102_000000_000.txt"
    locked.write_text("locked", encoding="utf-8")
    removable.write_text("old", encoding="utf-8")
    original_unlink = Path.unlink

    def unlink(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == locked:
            raise PermissionError("file is in use")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    traffic = TrafficLogger(
        LogConfig(
            enabled=True,
            save_to_file=True,
            dir=tmp_path,
            max_files=1,
        )
    )
    traffic.begin_connection("COM1")
    traffic.end_connection()

    assert locked.exists()
    assert not removable.exists()
    traffic.close()
