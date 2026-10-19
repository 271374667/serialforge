"""M6 device discovery contracts with isolated simulated serial ports."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from serialforge import (
    CommandSpec,
    DeviceInfo,
    DeviceProfile,
    ScanMode,
)
from serialforge.advanced import FramingConfig, RuntimeConfig, SerialConfig
from serialforge.discovery import DeviceFinder
from serialforge.discovery.baud_cache import BaudCache
from serialforge.enums import FramingMode
from serialforge.errors import ConfigError, ProbeError
from serialforge.protocols import TransportProtocol
from serialforge.qt_core import QCoreApplication, Qt
from serialforge.transport import PortRegistry
from tests.support import FakeBackend, SimulatedDevice, use_fake_backend
from tests.support.recording_backend import RecordingBackend

_APP: QCoreApplication | None = None
VERSION = CommandSpec(
    "Version", r"Software version (?P<version>\d+\.\d+)", timeout_s=0.15
)


def app() -> QCoreApplication:
    """Keep the one QtCore application alive throughout these tests."""
    global _APP  # noqa: PLW0603 -- Qt permits one application per process.
    _APP = QCoreApplication.instance() or QCoreApplication([])
    return _APP


def profile() -> DeviceProfile:
    """Build a profile for unended version replies and two allowed rates."""
    return DeviceProfile(
        vid_pid=[(0x067B, 0x23A3)],
        baudrates=[9600, 115200],
        probe=VERSION,
        serial=SerialConfig(settle_time_s=0),
        framing=FramingConfig(mode=FramingMode.SILENCE_GAP, silence_gap_s=0.04),
        runtime=RuntimeConfig(probe_total_timeout_s=2),
    )


def test_find_filters_vid_pid_and_probes_unterminated_reply() -> None:
    """Accept only a matching profile and its actual working baudrate."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                0x067B,
                0x23A3,
                port="SIM11",
                baudrate=115200,
                responses={b"Version\r\n": [b"Software ver", b"sion 1.75"]},
            ),
            SimulatedDevice(
                0x1111,
                0x2222,
                port="OTHER",
                responses={b"Version\r\n": b"Software version 9.99"},
            ),
        ]
    )
    with use_fake_backend(backend):
        finder = DeviceFinder(profile())
        progress: list[tuple[str, int]] = []
        completed: list[list[DeviceInfo]] = []
        finder.probe_progress.connect(
            lambda port, baud: progress.append((port, baud))
        )
        finder.probe_finished.connect(completed.append)
        results = finder.find(ScanMode.ALL)
        app().processEvents()
        assert len(results) == 1
        assert results[0].port == "SIM11"
        assert results[0].baudrate == 115200
        assert results[0].response == b"Software version 1.75"
        assert results[0].elapsed_s is not None
        assert progress == [("SIM11", 9600), ("SIM11", 115200)]
        assert completed == [results]
        assert all(item.closed for item in backend.transports)
        assert not PortRegistry.is_owned("SIM11")


def test_cache_fallback_forget_and_atomic_persistence(tmp_path: Path) -> None:
    """Try cached rates first, recover stale hints, and persist a new rate."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                0x067B,
                0x23A3,
                port="SIM12",
                serial_number="ABC",
                baudrate=115200,
                responses={b"Version\r\n": b"Software version 2.10"},
            )
        ]
    )
    with use_fake_backend(backend):
        finder = DeviceFinder(profile())
        cache = BaudCache(tmp_path / "baud_cache.json")
        device = DeviceInfo(
            port="SIM12", vid=0x067B, pid=0x23A3, serial_number="ABC"
        )
        cache.remember(device, 9600)
        finder._cache = cache
        progress: list[int] = []
        finder.probe_progress.connect(lambda _port, baud: progress.append(baud))
        results = finder.find(ScanMode.FIRST_MATCH)
        app().processEvents()
        assert results[0].baudrate == 115200
        assert progress == [9600, 115200]
        assert BaudCache(tmp_path / "baud_cache.json").get(device) == 115200
        finder.forget(device)
        assert BaudCache(tmp_path / "baud_cache.json").get(device) is None


def test_occupied_port_is_skipped_without_opening_it() -> None:
    """Respect another handler's process-local port lease."""
    app()
    backend = FakeBackend([SimulatedDevice(0x067B, 0x23A3, port="SIM13")])
    owner = object()
    PortRegistry.acquire("SIM13", owner)
    try:
        with use_fake_backend(backend):
            assert DeviceFinder(profile()).find() == []
            assert backend.transports == []
    finally:
        PortRegistry.release("SIM13", owner)


def test_caller_thread_can_cancel_blocking_find_and_reuse_finder() -> None:
    """Cancel a caller-owned scan thread, join it and reuse the finder."""
    app()
    probing = threading.Event()

    def silent_reply(_request: bytes) -> None:
        probing.set()

    device = SimulatedDevice(0x067B, 0x23A3, port="SIM14", script=silent_reply)
    backend = FakeBackend([device])
    with use_fake_backend(backend):
        finder = DeviceFinder(replace(profile(), baudrates=[115200]))
        completed: list[list[DeviceInfo]] = []
        finder.probe_finished.connect(
            completed.append, Qt.ConnectionType.DirectConnection
        )
        with ThreadPoolExecutor(max_workers=1) as caller:
            future = caller.submit(finder.find)
            try:
                assert probing.wait(2)
                assert not future.done()
                with pytest.raises(ProbeError, match="already active"):
                    finder.find()
            finally:
                finder.cancel()
            assert future.result(timeout=2) == []
        assert completed == [[]]
        assert all(item.closed for item in backend.transports)
        assert not PortRegistry.is_owned("SIM14")
        device.script = None
        device.responses = {b"Version\r\n": b"Software version 1.75"}
        assert len(finder.find()) == 1
        assert len(completed) == 2


def test_probe_rates_are_serial_and_stop_at_first_valid_reply() -> None:
    """Keep enumeration off the caller and close each rate before the next."""
    app()
    caller_thread = threading.get_ident()
    backend = RecordingBackend(
        [
            SimulatedDevice(
                0x067B,
                0x23A3,
                port="RATES",
                baudrate=115200,
                responses={b"Version\r\n": b"Software version 1.75"},
            )
        ]
    )
    with use_fake_backend(backend):
        finder = DeviceFinder(
            replace(profile(), baudrates=[9600, 115200, 19200])
        )
        results = finder.find()
        assert results[0].baudrate == 115200
        assert [rate for _, rate, _ in backend.attempts] == [9600, 115200]
        # One handle serves both rates; the port is opened only once.
        assert len(backend.transports) == 1
        assert backend.overlapping_ports == []
        assert all(tid != caller_thread for tid in backend.enumeration_threads)
        assert all(tid != caller_thread for _, _, tid in backend.attempts)
        assert all(item.closed for item in backend.transports)
        # The same finder prefers the newly remembered allowed rate.
        backend.attempts.clear()
        assert finder.find()[0].baudrate == 115200
        assert [rate for _, rate, _ in backend.attempts] == [115200]


@pytest.mark.parametrize("mode", [ScanMode.ALL, ScanMode.FIRST_MATCH])
def test_ports_probe_in_parallel_and_scan_mode_controls_results(
    mode: ScanMode,
) -> None:
    """Probe ports concurrently and return all matches or the first match."""
    app()
    barrier = threading.Barrier(2)

    def reply(_request: bytes) -> bytes:
        barrier.wait(timeout=2)
        return b"Software version 1.75"

    backend = RecordingBackend(
        [
            SimulatedDevice(0x067B, 0x23A3, port="PARALLEL1", script=reply),
            SimulatedDevice(0x067B, 0x23A3, port="PARALLEL2", script=reply),
        ]
    )
    with use_fake_backend(backend):
        finder = DeviceFinder(replace(profile(), baudrates=[115200]))
        results = finder.find(mode)
        assert len(results) == (2 if mode is ScanMode.ALL else 1)
        assert len({tid for _, _, tid in backend.attempts}) == 2
        assert backend.overlapping_ports == []
        assert all(item.closed for item in backend.transports)
        assert not any(PortRegistry.is_owned(item.port) for item in results)


@pytest.mark.parametrize("mode", [ScanMode.ALL, ScanMode.FIRST_MATCH])
def test_first_match_cancels_other_rates_and_all_finishes_them(
    mode: ScanMode,
) -> None:
    """Finish pending ports in ALL and interrupt them after FIRST_MATCH."""
    app()
    slow_started = threading.Event()

    def fast_reply(_request: bytes) -> bytes:
        assert slow_started.wait(2)
        return b"Software version 1.75"

    def slow_reply(_request: bytes) -> None:
        slow_started.set()

    backend = RecordingBackend(
        [
            SimulatedDevice(
                0x067B, 0x23A3, port="FAST", baudrate=115200, script=fast_reply
            ),
            SimulatedDevice(
                0x067B, 0x23A3, port="SLOW", baudrate=115200, script=slow_reply
            ),
        ]
    )
    with use_fake_backend(backend):
        finder = DeviceFinder(replace(profile(), baudrates=[115200, 9600]))
        results = finder.find(mode)
        assert [item.port for item in results] == ["FAST"]
        slow_rates = [
            rate for port, rate, _ in backend.attempts if port == "SLOW"
        ]
        assert slow_rates == (
            [115200, 9600] if mode is ScanMode.ALL else [115200]
        )
        assert all(item.closed for item in backend.transports)
        assert not PortRegistry.is_owned("SLOW")


def test_probe_pool_limit_and_total_timeout_close_all_handles() -> None:
    """Bound a long probe by the scan deadline and cancel queued ports."""
    app()
    slow_probe = CommandSpec("Version", VERSION.pattern, timeout_s=2.0)
    backend = RecordingBackend(
        [SimulatedDevice(0x067B, 0x23A3, port=f"TIMEOUT{i}") for i in range(4)]
    )
    with use_fake_backend(backend):
        finder = DeviceFinder(
            replace(
                profile(),
                probe=slow_probe,
                runtime=RuntimeConfig(
                    probe_pool_size=1, probe_total_timeout_s=0.08
                ),
            )
        )
        started = time.monotonic()
        assert finder.find() == []
        assert time.monotonic() - started < 1.0
        assert len(backend.attempts) == 1
        assert all(item.closed for item in backend.transports)
        assert not PortRegistry.is_owned("TIMEOUT0")


def test_named_port_bypasses_identity_filter_but_checks_probe() -> None:
    """Trust an explicitly selected port while still validating its reply."""
    app()
    device = SimulatedDevice(
        0x1111,
        0x2222,
        port="TRUSTED",
        baudrate=115200,
        responses={b"Version\r\n": b"Software version 1.75"},
    )
    backend = FakeBackend([device])
    with use_fake_backend(backend):
        finder = DeviceFinder(profile())
        assert finder.find() == []
        assert backend.transports == []
        result = finder.find(port="trusted")
        assert len(result) == 1
        assert result[0].vid == 0x1111
        assert result[0].baudrate == 115200
        device.responses = {b"Version\r\n": b"not a valid version"}
        assert finder.find(port="TRUSTED") == []
        assert all(item.closed for item in backend.transports)


def test_missing_whitelist_scans_all_ports_and_validates_reply() -> None:
    """Discover without VID/PID using the same baud and reply validation."""
    app()
    backend = RecordingBackend(
        [
            SimulatedDevice(
                None,
                None,
                port="NO-USB",
                baudrate=115200,
                responses={b"Version\r\n": b"Software version 1.75"},
            ),
            SimulatedDevice(
                0x1111,
                0x2222,
                port="USB",
                baudrate=115200,
                responses={b"Version\r\n": b"Software version 2.10"},
            ),
            SimulatedDevice(
                None,
                None,
                port="WRONG",
                responses={b"Version\r\n": b"unrecognized device"},
            ),
        ]
    )
    with use_fake_backend(backend):
        finder = DeviceFinder(replace(profile(), vid_pid=None))
        results = finder.find()
        assert {item.port for item in results} == {"NO-USB", "USB"}
        assert all(item.baudrate == 115200 for item in results)
        assert {port for port, _, _ in backend.attempts} == {
            "NO-USB",
            "USB",
            "WRONG",
        }
        assert all(item.closed for item in backend.transports)


def test_failing_ports_are_skipped_without_failing_the_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip a port that cannot be opened and still report the other match."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(0x067B, 0x23A3, port="BROKEN"),
            SimulatedDevice(
                0x067B,
                0x23A3,
                port="WORKING",
                baudrate=115200,
                responses={b"Version\r\n": b"Software version 1.75"},
            ),
        ]
    )
    open_port = backend.open

    def open_or_fail(
        port: str, params: Mapping[str, object]
    ) -> TransportProtocol:
        if port == "BROKEN":
            raise OSError("cannot open")
        return open_port(port, params)

    monkeypatch.setattr(backend, "open", open_or_fail)
    with use_fake_backend(backend):
        finder = DeviceFinder(replace(profile(), baudrates=[115200]))
        results = finder.find()
        assert [item.port for item in results] == ["WORKING"]
        assert all(item.closed for item in backend.transports)
        assert not PortRegistry.is_owned("BROKEN")
        assert not PortRegistry.is_owned("WORKING")


def test_all_ports_failing_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report no match when every candidate port is unusable."""
    app()
    backend = FakeBackend([SimulatedDevice(0x067B, 0x23A3, port="BROKEN")])

    def fail_open(port: str, params: Mapping[str, object]) -> TransportProtocol:
        raise OSError("cannot open")

    monkeypatch.setattr(backend, "open", fail_open)
    with use_fake_backend(backend):
        finder = DeviceFinder(replace(profile(), baudrates=[115200]))
        assert finder.find() == []
        assert backend.transports == []


def test_slow_open_is_abandoned_and_late_handle_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound one open by probe_open_timeout_s and clean up the late handle."""
    app()
    backend = FakeBackend([SimulatedDevice(0x067B, 0x23A3, port="SLOWOPEN")])
    open_port = backend.open

    def open_late(port: str, params: Mapping[str, object]) -> TransportProtocol:
        time.sleep(0.4)
        return open_port(port, params)

    monkeypatch.setattr(backend, "open", open_late)
    with use_fake_backend(backend):
        finder = DeviceFinder(
            replace(
                profile(),
                baudrates=[115200],
                runtime=RuntimeConfig(
                    probe_total_timeout_s=2,
                    probe_open_timeout_s=0.1,
                ),
            )
        )
        started = time.monotonic()
        assert finder.find() == []
        assert time.monotonic() - started < 0.35
        deadline = time.monotonic() + 2
        while not (
            backend.transports
            and backend.transports[-1].closed
            and not PortRegistry.is_owned("SLOWOPEN")
        ):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert len(backend.transports) == 1
        assert backend.transports[-1].closed


def test_activity_window_stops_silent_rates_early() -> None:
    """Skip a rate with no reply bytes instead of waiting for the timeout."""
    app()
    device = SimulatedDevice(
        0x067B,
        0x23A3,
        port="ACTIVITY",
        baudrate=115200,
        garbage_on_baud_mismatch=False,
        responses={b"Version\r\n": b"Software version 1.75"},
    )
    backend = FakeBackend([device])
    with use_fake_backend(backend):
        finder = DeviceFinder(
            replace(
                profile(),
                baudrates=[9600, 115200],
                probe=CommandSpec("Version", VERSION.pattern, timeout_s=1.0),
                runtime=RuntimeConfig(
                    probe_open_timeout_s=0.5,
                    probe_activity_window_s=0.1,
                    probe_total_timeout_s=3,
                ),
            )
        )
        started = time.monotonic()
        results = finder.find()
        elapsed = time.monotonic() - started
        assert results[0].baudrate == 115200
        assert elapsed < 0.8
        assert len(backend.transports) == 1
        assert all(item.closed for item in backend.transports)


@pytest.mark.parametrize(
    "build",
    [
        lambda: RuntimeConfig(probe_open_timeout_s=0),
        lambda: RuntimeConfig(probe_open_timeout_s=float("nan")),
        lambda: RuntimeConfig(probe_activity_window_s=-0.1),
        lambda: RuntimeConfig(probe_activity_window_s=float("inf")),
    ],
)
def test_probe_budget_validation(
    build: Callable[[], RuntimeConfig],
) -> None:
    """Reject invalid open budgets and activity windows at construction."""
    with pytest.raises(ConfigError):
        build()


@pytest.mark.parametrize(
    ("serial_number", "location"),
    [("SERIAL", None), (None, "USB-LOCATION"), (None, None)],
)
def test_cache_without_usb_identity_persists_and_prefers_stable_key(
    tmp_path: Path, serial_number: str | None, location: str | None
) -> None:
    """Remember unidentified devices by serial, location or case-folded port."""
    app()
    device = SimulatedDevice(
        None,
        None,
        port="NON-USB",
        serial_number=serial_number,
        location=location,
        baudrate=115200,
        responses={b"Version\r\n": b"Software version 1.75"},
    )
    backend = RecordingBackend([device])
    path = tmp_path / "baud_cache.json"
    with use_fake_backend(backend):
        finder = DeviceFinder(replace(profile(), vid_pid=None))
        finder._cache = BaudCache(path)
        result = finder.find()[0]
        assert [rate for _, rate, _ in backend.attempts] == [9600, 115200]
        assert BaudCache(path).get(result) == 115200
        new_port = "MOVED" if serial_number or location else "non-usb"
        device.port = new_port
        backend.attempts.clear()
        finder._cache = BaudCache(path)
        moved = finder.find()[0]
        assert moved.port == new_port
        assert [rate for _, rate, _ in backend.attempts] == [115200]
        finder.forget(moved)
        assert BaudCache(path).get(moved) is None
