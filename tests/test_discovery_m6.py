"""M6 device discovery contracts with isolated simulated serial ports."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication

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
from serialforge.transport import PortRegistry
from tests.support import FakeBackend, SimulatedDevice, use_fake_backend

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


def test_async_cancel_finishes_and_closes_probe_handle() -> None:
    """Cooperatively interrupt a waiting probe and publish one result."""
    app()
    backend = FakeBackend([SimulatedDevice(0x067B, 0x23A3, port="SIM14")])
    with use_fake_backend(backend):
        finder = DeviceFinder(profile())
        completed: list[list[DeviceInfo]] = []
        finder.probe_finished.connect(completed.append)
        finder.find_async()
        deadline = time.monotonic() + 2
        while not backend.transports and time.monotonic() < deadline:
            app().processEvents()
            time.sleep(0.005)
        assert backend.transports
        finder.cancel()
        while not completed and time.monotonic() < deadline:
            app().processEvents()
            time.sleep(0.005)
        assert completed == [[]]
        assert all(item.closed for item in backend.transports)
        assert not PortRegistry.is_owned("SIM14")
