"""M5 connection lifecycle tests using only the in-memory serial backend."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from serialforge import (
    CommandResult,
    CommandSpec,
    CommandStatus,
    ConnectionState,
    DeviceInfo,
    DeviceProfile,
    LogConfig,
)
from serialforge.advanced import FramingConfig, RuntimeConfig, SerialConfig
from serialforge.connection.handler_core import HandlerCore as SerialHandler
from serialforge.enums import FramingMode
from serialforge.errors import PortNotFoundError, SerialForgeError
from serialforge.qt_core import QCoreApplication, Qt
from serialforge.transport import PortRegistry
from tests.support import FakeBackend, SimulatedDevice, use_fake_backend
from tests.support.recording_backend import RecordingBackend


@dataclass(frozen=True)
class VersionInfo:
    """Hold a variable version reported by the simulated device."""

    version: str


VERSION = CommandSpec(
    "Version", r"Software version (?P<version>\d+\.\d+)", VersionInfo
)
_APP: QCoreApplication | None = None


def app() -> QCoreApplication:
    """Retain one core application for Qt signal delivery in this module."""
    global _APP  # noqa: PLW0603 -- Qt allows one application per process.
    _APP = QCoreApplication.instance() or QCoreApplication([])
    return _APP


def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Pump queued Qt signals until an observable condition is true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app().processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def profile() -> DeviceProfile:
    """Describe the COM11 reply shape without touching a real port."""
    return DeviceProfile(
        vid_pid=[(0x067B, 0x23A3)],
        baudrates=[115200],
        probe=VERSION,
        serial=SerialConfig(settle_time_s=0),
        framing=FramingConfig(mode=FramingMode.SILENCE_GAP, silence_gap_s=0.08),
    )


@pytest.mark.parametrize("version", ["1.02", "1.75"])
def test_direct_connection_matches_unterminated_version(version: str) -> None:
    """Send CRLF and match a fragmented reply with no receive suffix."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                0x067B,
                0x23A3,
                port="SIM1",
                responses={
                    b"Version\r\n": [
                        b"Software ver",
                        f"sion {version}".encode(),
                    ]
                },
            )
        ]
    )
    with use_fake_backend(backend):
        handler = SerialHandler(profile())
        handler.register(VERSION)
        results: list[CommandResult] = []
        states: list[ConnectionState] = []
        handler.command_finished.connect(results.append)
        handler.connection_state_changed.connect(states.append)
        handler.connect(DeviceInfo(port="SIM1", baudrate=115200))
        try:
            assert handler.state is ConnectionState.CONNECTED
            ticket = handler.send(VERSION)
            wait_until(lambda: len(results) == 1)
            assert results[0].status is CommandStatus.OK
            assert results[0].data == VersionInfo(version)
            assert results[0].spec is VERSION
            assert results[0].request_id == ticket.request_id
            assert ConnectionState.CONNECTING in states
            assert ConnectionState.CONNECTED in states
        finally:
            handler.disconnect()
            handler.disconnect()
        assert handler.state is ConnectionState.DISCONNECTED
        assert all(item.closed for item in backend.transports)
        assert not PortRegistry.is_owned("SIM1")


def test_init_sequence_finishes_before_connected_state() -> None:
    """Run registered initialization commands ahead of normal sends."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                0x067B,
                0x23A3,
                port="SIM2",
                responses={b"Version\r\n": b"Software version 1.02"},
            )
        ]
    )
    with use_fake_backend(backend):
        handler = SerialHandler(profile())
        handler.register(VERSION)
        handler.set_init_sequence([VERSION])
        completed: list[CommandResult] = []
        write_counts_at_connected: list[int] = []
        handler.command_finished.connect(completed.append)
        handler.connection_state_changed.connect(
            lambda state: (
                write_counts_at_connected.append(
                    backend.transports[-1].write_count
                )
                if state is ConnectionState.CONNECTED
                else None
            )
        )
        handler.connect(DeviceInfo(port="SIM2", baudrate=115200))
        try:
            wait_until(lambda: write_counts_at_connected == [1])
            assert write_counts_at_connected == [1]
            assert len(completed) == 1
            assert completed[0].status is CommandStatus.OK
            assert completed[0].spec is VERSION
        finally:
            handler.disconnect()


def test_unexpected_write_loss_reconnects_without_new_log_file(
    tmp_path: Path,
) -> None:
    """Recover a broken handle while retaining the connection log session."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                0x067B,
                0x23A3,
                port="SIM3",
                responses={b"Version\r\n": b"Software version 1.75"},
                disconnect_after_writes=1,
            )
        ]
    )
    runtime = RuntimeConfig(reconnect_initial_s=0.02, reconnect_max_s=0.05)
    with use_fake_backend(backend):
        handler = SerialHandler(
            replace(profile(), runtime=runtime),
            log=LogConfig(enabled=True, save_to_file=True, dir=tmp_path),
        )
        handler.register(VERSION)
        states: list[ConnectionState] = []
        results: list[CommandResult] = []
        handler.connection_state_changed.connect(states.append)
        handler.command_finished.connect(results.append)
        handler.connect(DeviceInfo(port="SIM3", baudrate=115200))
        try:
            assert handler.state is ConnectionState.CONNECTED
            handler.send(VERSION)
            wait_until(lambda: len(results) == 1)
            handler.send(VERSION)
            wait_until(lambda: ConnectionState.RECONNECTING in states)
            wait_until(
                lambda: (
                    len(backend.transports) >= 2
                    and handler.state is ConnectionState.CONNECTED
                )
            )
            assert results[1].status is CommandStatus.DISCONNECTED
        finally:
            handler.disconnect()
        assert len(list(tmp_path.glob("*.txt"))) == 1
        assert "RECONNECTED" in next(tmp_path.glob("*.txt")).read_text(
            encoding="utf-8"
        )
        assert all(item.closed for item in backend.transports)
        assert not PortRegistry.is_owned("SIM3")


def test_initial_open_failure_does_not_auto_reconnect() -> None:
    """Publish FAILED once when the requested port cannot be opened."""
    app()
    backend = FakeBackend()
    with use_fake_backend(backend):
        handler = SerialHandler(profile())
        errors: list[SerialForgeError] = []
        handler.error_occurred.connect(errors.append)
        try:
            with pytest.raises(SerialForgeError, match="port not found"):
                handler.connect(DeviceInfo(port="MISSING", baudrate=115200))
            assert handler.state is ConnectionState.FAILED
            app().processEvents()
            assert len(errors) == 1
            assert len(backend.transports) == 0
            assert not PortRegistry.is_owned("MISSING")
        finally:
            handler.disconnect()


def test_heartbeat_timeout_reconnects_and_manual_disconnect_stops() -> None:
    """Detect a silent device and stop retrying after explicit disconnect."""
    app()
    fast_version = CommandSpec(
        "Version", r"Software version (?P<version>\d+\.\d+)", timeout_s=0.12
    )
    backend = FakeBackend([SimulatedDevice(0x067B, 0x23A3, port="SIM4")])
    runtime = RuntimeConfig(reconnect_initial_s=0.02, reconnect_max_s=0.05)
    with use_fake_backend(backend):
        handler = SerialHandler(
            replace(
                profile(),
                probe=fast_version,
                heartbeat_s=0.05,
                runtime=runtime,
            )
        )
        states: list[ConnectionState] = []
        handler.connection_state_changed.connect(states.append)
        handler.connect(DeviceInfo(port="SIM4", baudrate=115200))
        try:
            assert handler.state is ConnectionState.CONNECTED
            wait_until(lambda: ConnectionState.RECONNECTING in states)
        finally:
            handler.disconnect()
        opened = len(backend.transports)
        time.sleep(0.1)
        assert handler.state is ConnectionState.DISCONNECTED
        assert len(backend.transports) == opened
        assert all(item.closed for item in backend.transports)


@pytest.mark.parametrize("target", [None, "SIM21"])
def test_discovery_connection_entry_points(target: str | None) -> None:
    """Resolve a profile or named port before opening the live connection."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                0x067B,
                0x23A3,
                port="SIM21",
                baudrate=115200,
                responses={b"Version\r\n": b"Software version 1.02"},
            )
        ]
    )
    with use_fake_backend(backend):
        handler = SerialHandler(profile())
        handler.register(VERSION)
        states: list[ConnectionState] = []
        results: list[CommandResult] = []
        handler.connection_state_changed.connect(states.append)
        handler.command_finished.connect(results.append)
        handler.connect(target)
        try:
            assert handler.state is ConnectionState.CONNECTED
            app().processEvents()
            assert states[:3] == [
                ConnectionState.PROBING,
                ConnectionState.CONNECTING,
                ConnectionState.CONNECTED,
            ]
            assert len(backend.transports) == 2
            assert backend.transports[0].closed
            handler.send(VERSION)
            wait_until(lambda: len(results) == 1)
            assert results[0].data == VersionInfo("1.02")
        finally:
            handler.disconnect()


def test_connect_waits_for_init_without_qt_events() -> None:
    """Keep connect blocked in a caller thread until initialization finishes."""
    app()
    init_started = threading.Event()
    allow_init = threading.Event()

    def init_reply(_request: bytes) -> bytes:
        init_started.set()
        assert allow_init.wait(2)
        return b"Software version 1.75"

    backend = RecordingBackend(
        [SimulatedDevice(0x067B, 0x23A3, port="INIT", script=init_reply)]
    )
    with use_fake_backend(backend):
        handler = SerialHandler(profile())
        handler.register(VERSION)
        handler.set_init_sequence([VERSION])
        results: list[CommandResult] = []
        handler.command_finished.connect(
            results.append, Qt.ConnectionType.DirectConnection
        )
        with ThreadPoolExecutor(max_workers=1) as caller:
            future = caller.submit(
                handler.connect, DeviceInfo(port="INIT", baudrate=115200)
            )
            try:
                assert init_started.wait(2)
                assert not future.done()
                assert handler.state is ConnectionState.CONNECTING
                with pytest.raises(SerialForgeError, match="cannot connect"):
                    handler.connect()
            finally:
                allow_init.set()
            try:
                assert future.result(timeout=2) is None
                assert handler.state is ConnectionState.CONNECTED
                assert len(results) == 1
                assert results[0].status is CommandStatus.OK
                assert results[0].spec is VERSION
                # Connection and this send need no processEvents or app.exec.
                result = handler.send_and_wait(VERSION, timeout=2)
                assert result.status is CommandStatus.OK
                assert result.spec is VERSION
            finally:
                handler.disconnect()
        assert all(item.closed for item in backend.transports)


def test_no_matching_device_raises_before_connect_returns() -> None:
    """Report discovery failure synchronously and allow another attempt."""
    app()
    backend = FakeBackend()
    with use_fake_backend(backend):
        handler = SerialHandler(profile())
        try:
            for _ in range(2):
                with pytest.raises(PortNotFoundError, match="no matching"):
                    handler.connect()
                assert handler.state is ConnectionState.FAILED
                assert backend.transports == []
        finally:
            handler.disconnect()


def test_blocking_connect_to_named_port_trusts_identity_and_is_ready() -> None:
    """Trust the named port even if its USB identity is outside the profile."""
    app()
    backend = RecordingBackend(
        [
            SimulatedDevice(
                0x1111,
                0x2222,
                port="NAMED",
                baudrate=115200,
                responses={b"Version\r\n": b"Software version 1.75"},
            )
        ]
    )
    with use_fake_backend(backend):
        handler = SerialHandler(profile())
        handler.register(VERSION)
        handler.connect("NAMED")
        try:
            assert handler.state is ConnectionState.CONNECTED
            assert handler.send_and_wait(VERSION, timeout=2).ok
            assert len(backend.transports) == 2
            assert backend.transports[0].closed
            assert all(
                tid != threading.get_ident() for _, _, tid in backend.attempts
            )
        finally:
            handler.disconnect()


def test_disconnect_cancels_a_callers_blocking_connection() -> None:
    """Unblock a pending connect only after its probe handles are closed."""
    app()
    probing = threading.Event()

    def silent_reply(_request: bytes) -> None:
        probing.set()

    backend = FakeBackend(
        [SimulatedDevice(0x067B, 0x23A3, port="CANCEL", script=silent_reply)]
    )
    with use_fake_backend(backend):
        handler = SerialHandler(profile())
        with ThreadPoolExecutor(max_workers=1) as caller:
            future = caller.submit(handler.connect)
            try:
                assert probing.wait(2)
                assert not future.done()
            finally:
                handler.disconnect()
            with pytest.raises(SerialForgeError, match="cancelled"):
                future.result(timeout=2)
        assert handler.state is ConnectionState.DISCONNECTED
        assert all(item.closed for item in backend.transports)
        assert not PortRegistry.is_owned("CANCEL")


def test_no_usb_auto_connect_allows_immediate_send() -> None:
    """Identify a non-USB device using only baudrates and the probe command."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                None,
                None,
                port="NO-USB",
                baudrate=115200,
                responses={b"Version\r\n": b"Software version 1.75"},
            )
        ]
    )
    no_usb_profile = DeviceProfile(baudrates=[115200], probe=VERSION)
    with use_fake_backend(backend):
        handler = SerialHandler(
            replace(
                no_usb_profile,
                serial=profile().serial,
                framing=profile().framing,
            )
        )
        handler.register(VERSION)
        handler.connect()
        try:
            assert handler.state is ConnectionState.CONNECTED
            result = handler.send_and_wait(VERSION, timeout=2)
            assert result.ok
            assert result.data == VersionInfo("1.75")
        finally:
            handler.disconnect()
        assert all(item.closed for item in backend.transports)


def test_connection_lost_during_init_raises_after_startup_cleanup() -> None:
    """Fail the initial attempt synchronously if initialization loses I/O."""
    app()

    def failed_init(_request: bytes) -> bytes:
        raise OSError("initialization write failed")

    backend = FakeBackend(
        [SimulatedDevice(0x067B, 0x23A3, port="INIT-LOSS", script=failed_init)]
    )
    with use_fake_backend(backend):
        handler = SerialHandler(profile())
        handler.register(VERSION)
        handler.set_init_sequence([VERSION])
        try:
            with pytest.raises(SerialForgeError, match="during initialization"):
                handler.connect(DeviceInfo(port="INIT-LOSS", baudrate=115200))
            assert handler.state is ConnectionState.FAILED
            assert len(backend.transports) == 1
            assert all(item.closed for item in backend.transports)
            assert not PortRegistry.is_owned("INIT-LOSS")
        finally:
            handler.disconnect()


def test_reconnect_queues_new_command_and_retries_idempotent_one() -> None:
    """Preserve ticket completion while sending safe work after recovery."""
    app()
    attempts = [0]

    def scripted_response(_request: bytes) -> bytes:
        attempts[0] += 1
        if attempts[0] == 1:
            raise OSError("one simulated write failure")
        return b"Software version 1.75"

    safe_version = CommandSpec(
        "Version",
        r"Software version (?P<version>\d+\.\d+)",
        idempotent=True,
    )
    backend = FakeBackend(
        [
            SimulatedDevice(
                0x067B, 0x23A3, port="SIM22", script=scripted_response
            )
        ]
    )
    runtime = RuntimeConfig(
        resend_idempotent=True,
        queue_while_reconnecting=True,
        reconnect_initial_s=0.1,
        reconnect_max_s=0.1,
    )
    with use_fake_backend(backend):
        handler = SerialHandler(replace(profile(), runtime=runtime))
        handler.register(safe_version)
        results: list[CommandResult] = []
        handler.command_finished.connect(results.append)
        handler.connect(DeviceInfo(port="SIM22", baudrate=115200))
        try:
            assert handler.state is ConnectionState.CONNECTED
            first = handler.send(safe_version)
            wait_until(lambda: handler.state is ConnectionState.RECONNECTING)
            queued = handler.send(safe_version)
            deadline = time.monotonic() + 3
            while len(results) < 3 and time.monotonic() < deadline:
                app().processEvents()
                time.sleep(0.005)
            assert len(results) >= 3, (
                results,
                handler.state,
                attempts,
                len(backend.transports),
            )
            assert [item.status for item in results].count(
                CommandStatus.DISCONNECTED
            ) == 1
            assert [item.status for item in results].count(
                CommandStatus.OK
            ) == 2
            assert any(item.request_id == first.request_id for item in results)
            assert any(item.request_id == queued.request_id for item in results)
            assert all(item.spec is safe_version for item in results)
        finally:
            handler.disconnect()
