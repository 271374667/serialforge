"""Validate the public facade using deterministic serial substitutes."""

from __future__ import annotations

import gc
import sys
import threading
import time
import weakref
from collections.abc import Callable, Iterator, Mapping
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from serialforge import (
    Correlation,
    DeviceInfo,
    DeviceProfile,
    Message,
    MessageCategory,
    ResponseMode,
    ScanMode,
    SerialForge,
    SerialForgeError,
    SerialHandler,
    Spec,
)
from serialforge.advanced import FramingConfig, SerialConfig
from serialforge.enums import ConnectionState, FramingMode, SpecRole
from serialforge.errors import (
    CommandCancelledError,
    CommandDisconnectedError,
    CommandError,
    CommandNotReadyError,
    CommandParseError,
    CommandTimeoutError,
    ConfigError,
)
from serialforge.protocols import TransportProtocol
from serialforge.qt_core import QCoreApplication, QThread
from serialforge.transport import BackendSwitch, PortRegistry
from tests.support import FakeBackend, SimulatedDevice, use_fake_backend

_APP: QCoreApplication | None = None


def app() -> QCoreApplication:
    """Retain a core application without importing GUI modules."""
    global _APP
    _APP = QCoreApplication.instance() or QCoreApplication([])
    return _APP


def wait(predicate: Callable[[], bool], timeout: float = 2) -> None:
    """Pump queued main-thread delivery until an assertion can be evaluated."""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app().processEvents()
        time.sleep(0.002)
    assert predicate()


@pytest.fixture
def device() -> Iterator[SerialForge]:
    """Open a deterministic device with response, event and no-response
    paths.
    """
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                None,
                None,
                port="FACADE",
                baudrate=115200,
                responses={
                    b"Probe\r\n": b"READY\r\n",
                    b"Read\r\n": b"VALUE:42\r\n",
                    b"Many\r\n": b"VALUE:1\r\nVALUE:2\r\n",
                    b"Bad\r\n": b"VALUE:bad\r\n",
                    b"BlankRead\r\n": b"\r\nVALUE:42\r\n\r\n",
                    b"Read 1\r\n": b"VALUE:1:41\r\n",
                    b"Read 2\r\n": b"VALUE:2:42\r\n",
                    b"SN_INFO_GET\r\n": [
                        b">",
                        b"SN_INFO_GET=B0002B",
                        b"\r",
                        b"\n",
                    ],
                },
            )
        ]
    )
    with use_fake_backend(backend):
        facade = SerialForge()
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        facade.connect_sync(DeviceInfo(port="FACADE"))
        app().processEvents()
        try:
            yield facade
        finally:
            facade.disconnect()
            app().processEvents()


def test_spec_defaults_roles_identity_and_freezing() -> None:
    """Keep value defaults typed and require explicit declaration variants."""
    command = Spec("Read", "VALUE:.*")
    assert command.mode is ResponseMode.SINGLE
    assert command.count == -1 and command.total_timeout_s == -1
    assert command.parser == "" and command.result_type is str
    assert command.role is SpecRole.COMMAND
    event = Spec(pattern="ALARM:.*")
    assert event.role is SpecRole.EVENT and event.request == ""
    assert event.mode is ResponseMode.NONE
    assert Spec("Stop", mode=ResponseMode.NO_REPLY).pattern == ""
    assert command != Spec("Read", "VALUE:.*")
    with pytest.raises(FrozenInstanceError):
        command.name = "changed"  # ty: ignore[invalid-assignment]


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"pattern": None},
        {"pattern": ""},
        {"pattern": ".*", "count": None},
        {"pattern": ".*", "count": 0},
        {"pattern": ".*", "parser": None},
        {"pattern": ".*", "result_type": None},
        {"pattern": ".*", "mode": None},
        {"pattern": ".*", "total_timeout_s": None},
        {"pattern": ".*", "timeout_s": float("nan")},
        {"pattern": ".*", "idle_timeout_s": True},
        {"pattern": ".*", "mode": ResponseMode.MULTI},
        {"pattern": ".*", "count": 2},
        {
            "pattern": ".*",
            "mode": ResponseMode.MULTI,
            "count": 2,
            "until": "END",
        },
    ],
)
def test_spec_rejects_invalid_values(fields: dict[str, Any]) -> None:
    """Reject None, missing patterns and ambiguous MULTI termination."""
    with pytest.raises(CommandError):
        Spec("Read", **fields)


def test_add_object_fields_and_atomic_configure(device: SerialForge) -> None:
    """Support both declaration forms and preserve identity across
    configuration.
    """
    spec = Spec("Read", "VALUE:.*")
    assert device.add(spec) is spec
    assert device.add(spec) is spec
    with pytest.raises(CommandError):
        device.add("Read", "VALUE:.*")
    with pytest.raises(ConfigError):
        device.configure(baudrates=[9600], probe="Probe", probe_pattern="READY")
    device.disconnect()
    device.configure(
        baudrates=[115200],
        probe="Probe",
        probe_pattern="READY",
        serial=SerialConfig(settle_time_s=0),
    )
    device.connect_sync(DeviceInfo(port="FACADE"))
    assert device.send_sync(spec).spec is spec
    device.remove(spec)
    with pytest.raises(CommandError):
        device.send_async(spec)


@pytest.mark.parametrize(
    ("target", "expected_port"),
    [
        (DeviceInfo(port="SECOND"), "SECOND"),
        (DeviceInfo(port="SECOND", baudrate=115200), "SECOND"),
        (DeviceInfo(port="FIRST"), "FIRST"),
        (DeviceInfo(port="FIRST", baudrate=115200), "FIRST"),
        ([], "FIRST"),
    ],
)
def test_connect_replaces_session_before_opening_target(
    target: DeviceInfo | list[DeviceInfo] | None,
    expected_port: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close the old worker before opening even the same device again."""
    app()
    devices = [
        SimulatedDevice(
            None,
            None,
            port=port,
            baudrate=115200,
            responses={
                b"Probe\r\n": b"READY\r\n",
                b"Init\r\n": b"INIT_OK\r\n",
                b"Read\r\n": f"VALUE:{port}\r\n".encode(),
            },
        )
        for port in (
            ["FIRST"] if expected_port == "FIRST" else ["FIRST", "SECOND"]
        )
    ]
    backend = FakeBackend(devices)
    with use_fake_backend(backend):
        facade = SerialForge()
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        init = facade.add("Init", "INIT_OK")
        read = facade.add("Read", "VALUE:.*")
        facade.set_init_sequence([init])
        try:
            facade.connect_sync(DeviceInfo(port="FIRST", baudrate=115200))
            core = facade._core
            assert core is not None and core._worker is not None
            old_worker = core._worker
            old_reader = old_worker._reader
            assert old_reader is not None
            old_transports = tuple(backend.transports)
            assert old_transports[-1].write_count == 1
            app().processEvents()
            states: list[ConnectionState] = []
            facade.connection_state_changed.connect(states.append)
            closed_before_open: list[bool] = []
            open_transport = backend.open

            def open_after_close(
                port: str, params: Mapping[str, object]
            ) -> TransportProtocol:
                closed_before_open.append(
                    all(item.closed for item in old_transports)
                )
                return open_transport(port, params)

            monkeypatch.setattr(backend, "open", open_after_close)
            assert facade.connect_sync(target) is None
            assert facade.state is ConnectionState.CONNECTED
            assert not old_worker.isRunning() and not old_reader.isRunning()
            assert closed_before_open and all(closed_before_open)
            assert all(item.closed for item in old_transports)
            assert sum(not item.closed for item in backend.transports) == 1
            assert backend.transports[-1].write_count == 1
            assert PortRegistry.is_owned(expected_port)
            if expected_port != "FIRST":
                assert not PortRegistry.is_owned("FIRST")
            result = facade.send_sync(read)
            assert result.spec is read and result.data == [
                f"VALUE:{expected_port}"
            ]
            app().processEvents()
            expected_states = [ConnectionState.DISCONNECTED]
            if not isinstance(target, DeviceInfo):
                expected_states.append(ConnectionState.PROBING)
            expected_states += [
                ConnectionState.CONNECTING,
                ConnectionState.CONNECTED,
            ]
            assert states == expected_states
        finally:
            facade.disconnect()
        assert all(item.closed for item in backend.transports)
        assert not PortRegistry.is_owned(expected_port)


def test_reconnect_cancels_old_calls_and_queued_callbacks(
    device: SerialForge,
) -> None:
    """Cancel pending commands and suppress successful callbacks on replace."""
    read = device.add("Read", "VALUE:.*")
    silent = device.add("Silent", "NEVER", timeout_s=2)
    callbacks: list[Message[Any]] = []
    completed = device.send_async(read).add_done_callback(callbacks.append)
    assert completed._done_event.wait(0.5)
    previous_result = completed.result()
    pending = device.send_async(silent).add_done_callback(callbacks.append)
    queued = device.send_async(read).add_done_callback(callbacks.append)
    device._on_raw_receive(b"old partial")
    assert device._receive_splitters

    device.connect_sync(DeviceInfo(port="FACADE"))

    for call in (pending, queued):
        assert call.done
        with pytest.raises(CommandCancelledError):
            call.result()
    assert completed.result() is previous_result
    assert not device._receive_splitters
    app().processEvents()
    assert not callbacks
    assert device.send_sync(read).data == ["VALUE:42"]


def test_failed_replacement_releases_old_device_and_allows_retry(
    device: SerialForge,
) -> None:
    """Keep no old connection after failure and accept a later connect."""
    core = device._core
    assert core is not None and core._worker is not None
    old_worker = core._worker
    with pytest.raises(SerialForgeError):
        device.connect_sync(DeviceInfo(port="MISSING", baudrate=115200))
    assert device.state is ConnectionState.FAILED
    assert not old_worker.isRunning()
    assert not PortRegistry.is_owned("FACADE")
    assert not PortRegistry.is_owned("MISSING")
    device.connect_sync(DeviceInfo(port="FACADE"))
    assert device.state is ConnectionState.CONNECTED
    assert device.send_sync(device.add("Read", "VALUE:.*")).data == ["VALUE:42"]


def test_connect_rejects_port_name_strings(device: SerialForge) -> None:
    """Port names change between sessions; only DeviceInfo is accepted."""
    with pytest.raises(ConfigError):
        # ty: ignore[invalid-argument-type] -- Exercise rejected port strings.
        device.connect_sync("FACADE")
    with pytest.raises(ConfigError):
        # ty: ignore[invalid-argument-type] -- Exercise rejected port strings.
        device.connect_async("FACADE")
    assert device.state is ConnectionState.CONNECTED


def test_connect_list_keeps_only_first_entry() -> None:
    """A non-empty target list connects its first DeviceInfo."""
    app()
    devices = [
        SimulatedDevice(
            None,
            None,
            port=f"LIST{index}",
            baudrate=115200,
            responses={
                b"Probe\r\n": b"READY\r\n",
                b"Read\r\n": f"VALUE:LIST{index}\r\n".encode(),
            },
        )
        for index in range(2)
    ]
    backend = FakeBackend(devices)
    with use_fake_backend(backend):
        facade = SerialForge()
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        read = facade.add("Read", "VALUE:.*")
        targets = [DeviceInfo(port="LIST1"), DeviceInfo(port="LIST0")]
        facade.connect_sync(targets)
        assert facade.state is ConnectionState.CONNECTED
        assert facade.send_sync(read).data == ["VALUE:LIST1"]
        assert PortRegistry.is_owned("LIST1")
        assert not PortRegistry.is_owned("LIST0")
        facade.disconnect()


def test_connect_empty_list_discovers_automatically() -> None:
    """An empty target list behaves the same as connect_sync()."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                None,
                None,
                port="FIRST",
                baudrate=115200,
                responses={b"Probe\r\n": b"READY\r\n"},
            )
        ]
    )
    with use_fake_backend(backend):
        facade = SerialForge()
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        facade.connect_sync([])
        assert facade.state is ConnectionState.CONNECTED
        assert PortRegistry.is_owned("FIRST")
        facade.disconnect()


def test_list_devices_returns_all_ports_without_probing() -> None:
    """list_devices enumerates unfiltered ports and needs no configure."""
    app()
    devices = [
        SimulatedDevice(0x1234, 0x5678, port="LISTED1", serial_number="A"),
        SimulatedDevice(0xABCD, 0xEF01, port="LISTED2", description="other"),
    ]
    backend = FakeBackend(devices)
    with use_fake_backend(backend):
        facade = SerialForge()
        found = facade.list_devices()
        assert [item.port for item in found] == ["LISTED1", "LISTED2"]
        assert found[0].vid == 0x1234 and found[0].pid == 0x5678
        assert all(item.baudrate is None for item in found)
        assert facade.state is ConnectionState.DISCONNECTED
        # 配置 VID/PID 后仍列出全部端口；已连接端口不再出现。
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            vid_pid=[(0x9999, 0x1111)],
            serial=SerialConfig(settle_time_s=0),
        )
        facade.connect_sync(DeviceInfo(port="LISTED1"))
        owned = facade.list_devices()
        assert [item.port for item in owned] == ["LISTED2"]
        facade.disconnect()


def test_sync_result_and_nonempty_receive_signal(device: SerialForge) -> None:
    """Retain command results while received publishes only actual bytes."""
    observed: list[Message[Any]] = []
    device.received.connect(observed.append)
    spec = device.add("Read", r"VALUE:(?P<value>\d+)")
    result = device.send_sync(spec, context={"widget": "row"})
    assert result.data == ["VALUE:42"] and result.spec is spec
    wait(lambda: bool(observed))
    assert result.context == {"widget": "row"}
    assert all(item is not result for item in observed)
    assert [item.raw_data for item in observed] == [b"VALUE:42\r\n"]
    assert (
        b"".join(
            item.raw_data
            for item in observed
            if item.category is MessageCategory.RAW_RECEIVE
        )
        == b"VALUE:42\r\n"
    )


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        ([b"", b"\r\n", b""], []),
        (
            [b">", b"SN_INFO_GET=B0002B", b"\r\n"],
            [b">SN_INFO_GET=B0002B\r\n"],
        ),
        (
            [b">SN_INFO_GET=B0002B\r", b"", b"\n"],
            [b">SN_INFO_GET=B0002B\r\n"],
        ),
        (
            [b"FIRST\r\nSECOND\r\nTH", b"IRD\r\n"],
            [b"FIRST\r\n", b"SECOND\r\n", b"THIRD\r\n"],
        ),
    ],
)
def test_raw_receive_emits_only_nonempty_complete_lines(
    chunks: list[bytes], expected: list[bytes]
) -> None:
    """Join fragmented reads and emit each line with its original ending."""
    app()
    facade = SerialForge()
    observed: list[Message[Any]] = []
    facade.received.connect(observed.append)
    for chunk in chunks:
        facade._on_raw_receive(chunk)
        app().processEvents()
        assert all(item.raw_data.endswith(b"\r\n") for item in observed)
    assert [item.raw_data for item in observed] == expected
    assert all(
        item.category is MessageCategory.RAW_RECEIVE for item in observed
    )
    facade.disconnect()


def test_raw_receive_uses_receive_terminator() -> None:
    """Keep receive line boundaries independent of transmit termination."""
    app()
    facade = SerialForge()
    facade.configure(
        baudrates=[115200],
        probe="Probe",
        probe_pattern="READY",
        terminator=";",
        framing=FramingConfig(terminator=b"\n"),
    )
    observed: list[Message[Any]] = []
    facade.received.connect(observed.append)
    facade._on_raw_receive(b"FIRST;SECOND")
    app().processEvents()
    assert not observed
    facade._on_raw_receive(b"\nTHIRD\n")
    app().processEvents()
    assert [item.raw_data for item in observed] == [
        b"FIRST;SECOND\n",
        b"THIRD\n",
    ]
    facade.disconnect()


@pytest.mark.parametrize("boundary", ["disconnect", "reconnect", "configure"])
def test_raw_receive_discards_partial_lines_at_connection_boundaries(
    boundary: str,
) -> None:
    """Prevent old partial reads from being prepended to a new connection."""
    app()
    facade = SerialForge()
    observed: list[Message[Any]] = []
    facade.received.connect(observed.append)
    facade._on_raw_receive(b"STALE")
    if boundary == "disconnect":
        facade.disconnect()
    elif boundary == "reconnect":
        facade._on_connection_state_changed(ConnectionState.RECONNECTING)
    else:
        facade.configure(
            baudrates=[115200], probe="Probe", probe_pattern="READY"
        )
    facade._on_raw_receive(b"FRESH\r\n")
    app().processEvents()
    assert [item.raw_data for item in observed] == [b"FRESH\r\n"]
    facade.disconnect()


def test_raw_receive_keeps_parallel_probe_lines_separate() -> None:
    """Avoid joining fragments read by different scanning threads."""
    app()
    facade = SerialForge()
    observed: list[Message[Any]] = []
    facade.received.connect(observed.append)
    barrier = threading.Barrier(2, timeout=2)

    def receive(prefix: bytes) -> None:
        facade._on_raw_receive(prefix)
        barrier.wait()
        facade._on_raw_receive(b"_READY\r\n")

    threads = [
        threading.Thread(target=receive, args=(prefix,))
        for prefix in (b"FIRST", b"SECOND")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    app().processEvents()
    assert sorted(item.raw_data for item in observed) == [
        b"FIRST_READY\r\n",
        b"SECOND_READY\r\n",
    ]
    facade.disconnect()


def test_raw_receive_preserves_non_line_protocol_chunks() -> None:
    """Keep no-terminator protocols observable without waiting for a line."""
    app()
    facade = SerialForge()
    facade.configure(
        baudrates=[115200],
        probe="Probe",
        probe_pattern="READY",
        framing=FramingConfig(mode=FramingMode.SILENCE_GAP, silence_gap_s=0.01),
    )
    observed: list[Message[Any]] = []
    facade.received.connect(observed.append)
    facade._on_raw_receive(b"")
    facade._on_raw_receive(b"READY")
    app().processEvents()
    assert [item.raw_data for item in observed] == [b"READY"]
    facade.disconnect()


def test_fragmented_serial_response_emits_one_raw_line(
    device: SerialForge,
) -> None:
    """Deliver one complete raw line through the real read-thread bridge."""
    observed: list[Message[Any]] = []
    device.received.connect(observed.append)
    spec = device.add("SN_INFO_GET", r">SN_INFO_GET=(?P<serial>\w+)")
    result = device.send_sync(spec)
    wait(lambda: bool(observed))
    assert [
        item.raw_data
        for item in observed
        if item.category is MessageCategory.RAW_RECEIVE
    ] == [b">SN_INFO_GET=B0002B\r\n"]
    assert result.data == [">SN_INFO_GET=B0002B"] and result.spec is spec


def test_received_never_emits_empty_raw_data_and_keeps_callbacks(
    device: SerialForge,
) -> None:
    """Filter every empty raw_data signal while retaining result callbacks."""
    observed: list[Message[Any]] = []
    callbacks: list[Message[Any]] = []
    device.received.connect(observed.append)
    spec = device.add("BlankRead", r"VALUE:(?P<value>\d+)", int)
    call = device.send_async(spec).add_done_callback(callbacks.append)
    wait(lambda: bool(callbacks) and bool(observed))
    assert device._core is not None
    device._core._dispatcher.flush_events()
    app().processEvents()
    assert callbacks[0] is call.result() and callbacks[0].data == [42]
    assert [item.raw_data for item in observed] == [b"VALUE:42\r\n"]
    assert all(item.raw_data for item in observed)
    assert callbacks[0] not in observed


@pytest.mark.parametrize("no_reply", [True, False])
def test_received_stays_silent_without_device_bytes(
    device: SerialForge, no_reply: bool
) -> None:
    """Suppress empty terminal observations without changing result access."""
    observed: list[Message[Any]] = []
    callbacks: list[Message[Any]] = []
    device.received.connect(observed.append)
    spec = (
        device.add("Stop", mode=ResponseMode.NO_REPLY)
        if no_reply
        else device.add("Silent", "NEVER")
    )
    call = device.send_async(spec, timeout_s=0.05)
    call.add_done_callback(callbacks.append)
    wait(lambda: call.done)
    app().processEvents()
    if no_reply:
        wait(lambda: bool(callbacks))
        assert callbacks[0] is call.result() and callbacks[0].ok
    else:
        with pytest.raises(CommandTimeoutError):
            call.result()
        assert not callbacks
    assert not observed


def test_callback_main_thread_closure_order_and_late_replay(
    device: SerialForge,
) -> None:
    """Queue closures on the application thread even when submitted by a
    worker.
    """
    spec = device.add("Read", r"VALUE:(?P<value>\d+)", dict)
    called: list[tuple[int, Message[Any], QThread]] = []
    calls: list[Any] = []

    def submit() -> None:
        call = device.send_async(spec, context={"row": 7})
        call.add_done_callback(
            lambda result: called.append((1, result, QThread.currentThread()))
        )
        call.add_done_callback(
            lambda result: called.append((2, result, QThread.currentThread()))
        )
        calls.append(call)

    thread = threading.Thread(target=submit)
    thread.start()
    thread.join()
    assert not called
    wait(lambda: len(called) == 2)
    call = calls[0]
    assert [item[0] for item in called] == [1, 2]
    assert all(item[2] == app().thread() for item in called)
    assert all(item[1] is call.result() for item in called)
    assert call.result().context is call.context
    assert call.result().data == [{"value": "42"}]
    call.add_done_callback(
        lambda result: called.append((3, result, QThread.currentThread()))
    )
    assert len(called) == 2
    wait(lambda: len(called) == 3)


def test_successful_result_survives_ui_deadline(device: SerialForge) -> None:
    """Expire queued closures without changing a success saved before
    deadline.
    """
    spec = device.add("Read", "VALUE:.*")
    called: list[Any] = []
    call = device.send_async(spec, timeout_s=0.15).add_done_callback(
        called.append
    )
    assert call._done_event.wait(0.1)
    result = call.result()
    time.sleep(0.18)
    app().processEvents()
    assert call.result() is result and call.callback_expired and not called
    assert not call._callbacks


def test_timeout_no_callback_and_context_snapshot(device: SerialForge) -> None:
    """Bound queue/response time and retain independent shallow metadata."""
    spec = device.add("Silent", "NEVER", timeout_s=2)
    context: dict[str, object] = {"row": 1}
    called: list[Any] = []
    call = device.send_async(spec, timeout_s=0.04, context=context)
    call.add_done_callback(called.append)
    context["row"] = 99
    assert call._done_event.wait(0.3)
    with pytest.raises(CommandTimeoutError) as error:
        call.result()
    assert error.value.context == {"row": 1}
    assert error.value.spec is spec
    app().processEvents()
    assert not called and call.exception is error.value
    with pytest.raises(TypeError):
        call.context["row"] = 2  # ty: ignore[invalid-assignment]


def test_queued_timeout_is_never_transmitted(device: SerialForge) -> None:
    """Cancel an expired queued request before the preceding request
    completes.
    """
    blocker = device.add("Silent", "NEVER", timeout_s=1)
    queued = device.add("Read", "VALUE:.*")
    sent: list[bytes] = []
    device.raw_sent.connect(sent.append)
    first = device.send_async(blocker)
    second = device.send_async(queued, timeout_s=0.03)
    assert second._done_event.wait(0.2)
    first.cancel()
    wait(lambda: bool(sent))
    assert b"Read\r\n" not in sent
    with pytest.raises(CommandTimeoutError):
        second.result()


def test_immediate_failure_and_signal_surface() -> None:
    """Expose no legacy signals; retain immediate failures before send
    returns.
    """
    app()
    facade = SerialForge()
    facade.configure(baudrates=[115200], probe="Probe", probe_pattern="READY")
    spec = facade.add("Read", "VALUE:.*")
    call = facade.send_async(spec)
    assert call.done
    with pytest.raises(CommandDisconnectedError):
        call.result()
    with pytest.raises(CommandDisconnectedError):
        facade.send_sync(spec)
    for cls in (SerialForge, SerialHandler):
        for name in (
            "traffic_logged",
            "command_finished",
            "event_received",
            "error_occurred",
            "scan_finished",
            "scan_progress",
        ):
            assert not hasattr(cls, name)
    facade.disconnect()


def test_pending_and_stream_sync_rejection(device: SerialForge) -> None:
    """Reject premature reads and synchronous streams before transmitting."""
    stream = device.add("Stream", "VALUE:.*", mode=ResponseMode.STREAM)
    with pytest.raises(CommandError):
        device.send_sync(stream)
    call = device.send_async(stream, timeout_s=0.1)
    with pytest.raises(CommandNotReadyError):
        call.result()
    call.cancel()
    assert call.done and call.exception is not None


@pytest.mark.parametrize(
    "configuration", ["saved", "profile", "spec_fields", "text_fields"]
)
def test_scan_closes_connection_before_configuring_and_probing(
    device: SerialForge,
    configuration: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Release the current device before scanning and leave it disconnected."""
    backend = cast(FakeBackend, BackendSwitch.current())
    old_transports = tuple(backend.transports)
    core = device._core
    assert core is not None and core._worker is not None
    old_worker = core._worker
    old_reader = old_worker._reader
    assert old_reader is not None
    old_profile = device._profile
    probe = device.add("Probe", "READY")
    read = device.add("Read", "VALUE:.*")
    device.set_init_sequence([read])
    open_transport = backend.open
    closed_before_probe: list[bool] = []

    def open_after_close(
        port: str, params: Mapping[str, object]
    ) -> TransportProtocol:
        closed_before_probe.append(all(item.closed for item in old_transports))
        return open_transport(port, params)

    monkeypatch.setattr(backend, "open", open_after_close)
    profile = DeviceProfile(
        baudrates=[115200], probe=probe, serial=SerialConfig(settle_time_s=0)
    )
    if configuration == "saved":
        found = device.scan_sync()
        assert device._profile is old_profile
    elif configuration == "profile":
        found = device.scan_sync(profile)
        assert device._profile is profile
    elif configuration == "spec_fields":
        found = device.scan_sync(
            baudrates=[115200],
            probe=probe,
            serial=SerialConfig(settle_time_s=0),
        )
        assert device._profile is not None and device._profile.probe is probe
    else:
        found = device.scan_sync(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
    assert [item.port for item in found] == ["FACADE"]
    assert device.state is ConnectionState.DISCONNECTED
    assert not old_worker.isRunning() and not old_reader.isRunning()
    assert closed_before_probe and all(closed_before_probe)
    assert all(item.closed for item in backend.transports)
    assert not PortRegistry.is_owned("FACADE")
    assert device._core is not None and device._core._init_sequence == (read,)
    device.connect_sync(found[0])
    assert device.send_sync(read).spec is read
    assert device.send_sync(read).data == ["VALUE:42"]


def test_scan_cancels_previous_calls_and_callbacks(device: SerialForge) -> None:
    """Clear old commands, successful callbacks and partial receive lines."""
    read = device.add("Read", "VALUE:.*")
    silent = device.add("Silent", "NEVER", timeout_s=2)
    callbacks: list[Message[Any]] = []
    completed = device.send_async(read).add_done_callback(callbacks.append)
    assert completed._done_event.wait(0.5)
    result = completed.result()
    pending = device.send_async(silent).add_done_callback(callbacks.append)
    queued = device.send_async(read).add_done_callback(callbacks.append)
    device._on_raw_receive(b"old partial")

    assert device.scan_sync()

    for call in (pending, queued):
        with pytest.raises(CommandCancelledError):
            call.result()
    assert completed.result() is result
    assert not device._calls and not device._receive_splitters
    app().processEvents()
    assert not callbacks


def test_scan_stays_disconnected_without_matching_devices(
    device: SerialForge,
) -> None:
    """Return no matches after releasing the old device and accept a retry."""
    assert (
        device.scan_sync(
            baudrates=[9600],
            probe="Probe",
            probe_pattern="READY",
            probe_timeout_s=0.02,
            serial=SerialConfig(settle_time_s=0),
        )
        == []
    )
    assert device.state is ConnectionState.DISCONNECTED
    assert not PortRegistry.is_owned("FACADE")
    assert not device._connecting and not device._scanning
    assert device.scan_sync(
        baudrates=[115200],
        probe="Probe",
        probe_pattern="READY",
        serial=SerialConfig(settle_time_s=0),
    )


def test_scan_aborts_when_old_connection_cannot_be_stopped(
    device: SerialForge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep configuration intact and release operation guards on stop errors."""
    core = device._core
    assert core is not None
    old_profile = device._profile
    backend = cast(FakeBackend, BackendSwitch.current())
    old_transports = tuple(backend.transports)

    def fail_disconnect() -> None:
        raise SerialForgeError("connection worker did not stop")

    with monkeypatch.context() as patch:
        patch.setattr(core, "disconnect", fail_disconnect)
        with pytest.raises(SerialForgeError, match="did not stop"):
            device.scan_sync(
                baudrates=[9600], probe="Probe", probe_pattern="READY"
            )
    assert device._profile is old_profile
    assert tuple(backend.transports) == old_transports
    assert device.state is ConnectionState.CONNECTED
    assert not device._connecting and not device._scanning
    assert device.scan_sync()


@pytest.mark.parametrize("failure", ["configuration", "probe"])
def test_scan_failure_leaves_old_device_closed_and_allows_retry(
    device: SerialForge, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Release operation guards after invalid configuration or probe I/O."""
    backend = cast(FakeBackend, BackendSwitch.current())
    old_profile = device._profile

    def fail_open(port: str, params: Mapping[str, object]) -> TransportProtocol:
        raise OSError("probe transport unavailable")

    with monkeypatch.context() as patch:
        if failure == "configuration":
            with pytest.raises(ConfigError):
                device.scan_sync(
                    baudrates=[], probe="Probe", probe_pattern="READY"
                )
        else:
            patch.setattr(backend, "open", fail_open)
            assert device.scan_sync() == []
    assert device._profile is old_profile
    assert device.state is ConnectionState.DISCONNECTED
    assert not PortRegistry.is_owned("FACADE")
    assert all(item.closed for item in backend.transports)
    assert not device._connecting and not device._scanning
    assert device.scan_sync()


@pytest.mark.parametrize("phase", ["disconnect", "probe"])
def test_scan_rejects_concurrent_lifecycle_operations(
    device: SerialForge, phase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reserve both connection cleanup and probing without blocking readers."""
    entered = threading.Event()
    release = threading.Event()
    failures: list[Exception] = []
    backend = cast(FakeBackend, BackendSwitch.current())
    open_transport = backend.open
    disconnect = device.disconnect

    def pause_disconnect() -> None:
        entered.set()
        assert release.wait(3)
        disconnect()

    def pause_probe(
        port: str, params: Mapping[str, object]
    ) -> TransportProtocol:
        entered.set()
        assert release.wait(3)
        return open_transport(port, params)

    if phase == "disconnect":
        monkeypatch.setattr(device, "disconnect", pause_disconnect)
    else:
        monkeypatch.setattr(backend, "open", pause_probe)

    def scan_in_thread() -> None:
        try:
            device.scan_sync(mode=ScanMode.FIRST_MATCH)
        except Exception as error:
            failures.append(error)

    thread = threading.Thread(target=scan_in_thread)
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(ConfigError):
            device.scan_sync()
        with pytest.raises(ConfigError):
            device.scan_sync(
                baudrates=[115200], probe="Probe", probe_pattern="READY"
            )
        with pytest.raises(ConfigError):
            device.connect_sync(DeviceInfo(port="FACADE"))
        with pytest.raises(ConfigError):
            device.configure(
                baudrates=[115200], probe="Probe", probe_pattern="READY"
            )
        if phase == "probe":
            device.cancel_scan()
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not failures
    assert device.state is ConnectionState.DISCONNECTED
    assert not device._scanning and not device._connecting
    assert all(item.closed for item in backend.transports)


def test_scan_returns_results_and_observes_probe_bytes() -> None:
    """Configure directly through scan fields and observe exact probe
    traffic.
    """
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                None, None, port="SCAN", responses={b"Probe\r\n": b"READY\r\n"}
            )
        ]
    )
    facade = SerialForge()
    sent: list[bytes] = []
    received: list[Message[Any]] = []
    facade.raw_sent.connect(sent.append)
    facade.received.connect(received.append)
    with use_fake_backend(backend):
        found = facade.scan_sync(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
    app().processEvents()
    assert [item.port for item in found] == ["SCAN"]
    assert sent == [b"Probe\r\n"]
    assert b"".join(item.raw_data for item in received) == b"READY\r\n"
    assert all(item.closed for item in backend.transports)
    facade.disconnect()


def test_scan_discards_unterminated_reads_from_previous_baudrate() -> None:
    """Start a fresh raw line after a failed probe on the same thread."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                None,
                None,
                port="SCAN-RATES",
                baudrate=115200,
                responses={b"Probe\r\n": [b"RE", b"ADY\r\n"]},
            )
        ]
    )
    facade = SerialForge()
    observed: list[Message[Any]] = []
    facade.received.connect(observed.append)
    with use_fake_backend(backend):
        found = facade.scan_sync(
            baudrates=[9600, 115200],
            probe="Probe",
            probe_pattern="READY",
            probe_timeout_s=0.05,
            serial=SerialConfig(settle_time_s=0),
        )
    app().processEvents()
    assert [item.port for item in found] == ["SCAN-RATES"]
    assert [item.raw_data for item in observed] == [b"READY\r\n"]
    assert all(item.closed for item in backend.transports)
    facade.disconnect()


def test_multi_parser_and_no_reply(device: SerialForge) -> None:
    """Parse named groups explicitly and collect counted responses."""
    multi = device.add(
        "Many", r"VALUE:(?P<value>\d+)", dict, mode=ResponseMode.MULTI, count=2
    )
    assert device.send_sync(multi).data == [{"value": "1"}, {"value": "2"}]
    stop = device.add("Stop", mode=ResponseMode.NO_REPLY)
    result = device.send_sync(stop)
    assert result.ok and result.data == [] and result.sent == b"Stop\r\n"


def test_error_queue_and_counter() -> None:
    """Bound unowned error retention and raise on caller-side inspection."""
    app()
    facade = SerialForge()
    for index in range(130):
        facade._on_error(CommandError(str(index)))
    assert facade.stats().dropped_background_errors == 2
    with pytest.raises(CommandError, match="^2$"):
        facade.check_errors()
    facade.disconnect()


def test_context_is_shared_with_async_result(device: SerialForge) -> None:
    """Keep call/callback context without emitting empty-byte metadata."""
    observed: list[Message[Any]] = []
    callbacks: list[Message[Any]] = []
    device.received.connect(observed.append)
    spec = device.add(
        "Many", r"VALUE:(?P<value>\d+)", dict, mode=ResponseMode.MULTI, count=2
    )
    call = device.send_async(spec, context={"row": 5})
    call.add_done_callback(callbacks.append)
    wait(lambda: bool(callbacks))
    assert callbacks[0] is call.result()
    assert callbacks[0].context is call.context
    assert [item.raw_data for item in observed] == [
        b"VALUE:1\r\n",
        b"VALUE:2\r\n",
    ]
    assert all(item.context == {} for item in observed)


def test_invalid_callback_signature_rejected(device: SerialForge) -> None:
    """Fail at registration when a function cannot consume one positional
    result.
    """
    call = device.send_async(device.add("Read", "VALUE:.*"))

    def empty() -> None:
        pass

    def two(first: object, second: object) -> None:
        pass

    def named(*, result: object) -> None:
        pass

    for callback in (empty, two, named):
        with pytest.raises(TypeError, match="one positional Message"):
            call.add_done_callback(callback)  # ty: ignore[invalid-argument-type]


def test_parse_failure_retains_cause_without_success_callback(
    device: SerialForge,
) -> None:
    """Preserve conversion errors as execution causes and suppress success
    paths.
    """
    spec = device.add("Bad", r"VALUE:(?P<value>.+)", int)
    received: list[Any] = []
    call = device.send_async(spec).add_done_callback(received.append)
    assert call._done_event.wait(0.2)
    with pytest.raises(CommandParseError) as error:
        call.result()
    assert error.value.cause is not None
    app().processEvents()
    assert not received


def test_temporary_call_chain_retained_and_cancel_after_success(
    device: SerialForge,
) -> None:
    """Retain chained calls until delivery and never rewrite successful
    results.
    """
    spec = device.add("Read", "VALUE:.*")
    received: list[Any] = []
    device.send_async(spec).add_done_callback(received.append)
    gc.collect()
    wait(lambda: bool(received))
    call = device.send_async(spec)
    assert call._done_event.wait(0.2)
    result = call.result()
    call.add_done_callback(received.append)
    call.cancel()
    app().processEvents()
    assert call.result() is result and len(received) == 1


def test_expiry_releases_captured_objects_without_event_loop(
    device: SerialForge,
) -> None:
    """Release closures on the timer thread while the application is blocked."""

    class Captured:
        """Represent an application-owned object retained only by a closure."""

    payload = Captured()
    reference = weakref.ref(payload)
    call = device.send_async(device.add("Read", "VALUE:.*"), timeout_s=0.12)
    call.add_done_callback(lambda message, captured=payload: None)
    del payload
    assert call._done_event.wait(0.1)
    time.sleep(0.15)
    gc.collect()
    assert reference() is None
    assert call.result().ok


def test_disconnect_cancels_pending_without_callback(
    device: SerialForge,
) -> None:
    """Resolve accepted work once and release its success subscribers on
    close.
    """
    spec = device.add("Silent", "NEVER", timeout_s=2)
    received: list[Any] = []
    call = device.send_async(spec).add_done_callback(received.append)
    device.disconnect()
    with pytest.raises(CommandCancelledError):
        call.result()
    app().processEvents()
    assert not received and not call._callbacks


def test_scan_io_fault_is_skipped_and_closes_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skip an unopenable port and return no match instead of raising."""
    app()
    backend = FakeBackend([SimulatedDevice(None, None, port="FAULT")])

    def fail(port: str, params: object) -> None:
        raise OSError("open failed")

    monkeypatch.setattr(backend, "open", fail)
    facade = SerialForge()
    with use_fake_backend(backend):
        assert (
            facade.scan_sync(
                baudrates=[115200],
                probe="Probe",
                probe_pattern="READY",
                serial=SerialConfig(settle_time_s=0),
            )
            == []
        )
    assert facade._scan_done.is_set()
    facade.disconnect()


def test_scan_async_requires_configuration() -> None:
    """Fail synchronously when no profile was configured and no fields came."""
    app()
    facade = SerialForge()
    with pytest.raises(ConfigError):
        facade.scan_async()


def test_scan_async_disconnects_the_current_session_first(
    device: SerialForge,
) -> None:
    """Rescan a connected facade by closing its session inside the worker."""
    seen: list[list[DeviceInfo]] = []
    call = device.scan_async()
    call.add_done_callback(seen.append)
    wait(lambda: call.done)
    wait(lambda: bool(seen))
    assert [item.port for item in seen[0]] == ["FACADE"]
    assert device.state is ConnectionState.DISCONNECTED


def test_connect_async_returns_immediately_and_calls_back() -> None:
    """Connect in a worker and hand the device to a main-thread closure."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                None,
                None,
                port="ASYNC-CONN",
                baudrate=115200,
                responses={b"Probe\r\n": b"READY\r\n"},
            )
        ]
    )
    main_thread = threading.get_ident()
    seen: list[tuple[int, DeviceInfo]] = []
    with use_fake_backend(backend):
        facade = SerialForge()
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        started = time.monotonic()
        call = facade.connect_async(DeviceInfo(port="ASYNC-CONN"))
        assert time.monotonic() - started < 0.2
        assert not call.done
        call.add_done_callback(
            lambda device: seen.append((threading.get_ident(), device))
        )
        wait(lambda: call.done)
        wait(lambda: bool(seen))
        assert seen[0][0] == main_thread
        assert seen[0][1].port == "ASYNC-CONN"
        assert call.result().port == "ASYNC-CONN"
        assert facade.state is ConnectionState.CONNECTED
        facade.disconnect()


def test_connect_async_failure_skips_callback_and_raises_from_result() -> None:
    """Keep success closures silent and retain the error for result()."""
    app()
    backend = FakeBackend([SimulatedDevice(None, None, port="ASYNC-CONN")])
    with use_fake_backend(backend):
        facade = SerialForge()
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        call = facade.connect_async(DeviceInfo(port="MISSING"))
        seen: list[DeviceInfo] = []
        call.add_done_callback(seen.append)
        wait(lambda: call.done, timeout=3)
        assert seen == []
        assert call.exception is not None
        with pytest.raises(SerialForgeError):
            call.result()
        facade.disconnect()


def test_connect_async_cancel_suppresses_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop a pending connection attempt without running success closures."""
    app()
    backend = FakeBackend(
        [SimulatedDevice(None, None, port="ASYNC-CONN-CANCEL")]
    )
    open_port = backend.open

    def slow_open(port: str, params: Mapping[str, object]) -> TransportProtocol:
        time.sleep(0.3)
        return open_port(port, params)

    monkeypatch.setattr(backend, "open", slow_open)
    with use_fake_backend(backend):
        facade = SerialForge()
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        call = facade.connect_async(DeviceInfo(port="ASYNC-CONN-CANCEL"))
        seen: list[DeviceInfo] = []
        call.add_done_callback(seen.append)
        time.sleep(0.05)
        call.cancel()
        wait(lambda: call.done, timeout=3)
        assert call.cancelled
        assert seen == []
        with pytest.raises(SerialForgeError):
            call.result()
        facade.disconnect()


def test_connect_async_rejects_concurrent_lifecycle_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reserve the facade while the asynchronous connection is pending."""
    app()
    backend = FakeBackend([SimulatedDevice(None, None, port="ASYNC-CONN-BUSY")])
    open_port = backend.open

    def slow_open(port: str, params: Mapping[str, object]) -> TransportProtocol:
        time.sleep(0.3)
        return open_port(port, params)

    monkeypatch.setattr(backend, "open", slow_open)
    with use_fake_backend(backend):
        facade = SerialForge()
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        call = facade.connect_async(DeviceInfo(port="ASYNC-CONN-BUSY"))
        with pytest.raises(ConfigError):
            facade.connect_async()
        with pytest.raises(ConfigError):
            facade.connect_sync()
        with pytest.raises(ConfigError):
            facade.scan_async()
        with pytest.raises(ConfigError):
            facade.configure(
                baudrates=[9600],
                probe="Probe",
                probe_pattern="READY",
            )
        call.cancel()
        wait(lambda: call.done, timeout=3)
        facade.disconnect()


def test_disconnect_during_connect_async_returns_without_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Join a pending asynchronous connection from another thread."""
    app()
    backend = FakeBackend([SimulatedDevice(None, None, port="ASYNC-CONN-JOIN")])
    open_port = backend.open

    def slow_open(port: str, params: Mapping[str, object]) -> TransportProtocol:
        time.sleep(0.3)
        return open_port(port, params)

    monkeypatch.setattr(backend, "open", slow_open)
    with use_fake_backend(backend):
        facade = SerialForge()
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        call = facade.connect_async(DeviceInfo(port="ASYNC-CONN-JOIN"))
        seen: list[DeviceInfo] = []
        call.add_done_callback(seen.append)
        time.sleep(0.05)
        started = time.monotonic()
        facade.disconnect()
        assert time.monotonic() - started < 3
        wait(lambda: call.done)
        assert call.cancelled
        assert seen == []


def test_scan_async_callback_can_start_the_next_scan() -> None:
    """Free the facade before callbacks so a closure may scan again."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                None,
                None,
                port="ASYNC-NEXT",
                baudrate=115200,
                responses={b"Probe\r\n": b"READY\r\n"},
            )
        ]
    )
    results: list[int] = []
    with use_fake_backend(backend):
        facade = SerialForge()

        def first_done(devices: list[DeviceInfo]) -> None:
            assert [item.port for item in devices] == ["ASYNC-NEXT"]
            second = facade.scan_async(
                baudrates=[115200],
                probe="Probe",
                probe_pattern="READY",
                serial=SerialConfig(settle_time_s=0),
            )
            second.add_done_callback(lambda again: results.append(len(again)))

        first = facade.scan_async(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        first.add_done_callback(first_done)
        wait(lambda: bool(results), timeout=3)
        assert results == [1]
        facade.disconnect()


def test_scan_async_returns_immediately_and_calls_back_on_main_thread() -> None:
    """Hand the discovered devices to a main-thread closure without blocking."""
    app()
    backend = FakeBackend(
        [
            SimulatedDevice(
                None,
                None,
                port="ASYNC",
                baudrate=115200,
                responses={b"Probe\r\n": b"READY\r\n"},
            )
        ]
    )
    main_thread = threading.get_ident()
    seen: list[tuple[int, list[DeviceInfo]]] = []
    with use_fake_backend(backend):
        facade = SerialForge()
        started = time.monotonic()
        call = facade.scan_async(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        assert time.monotonic() - started < 0.2
        assert not call.done
        call.add_done_callback(
            lambda devices: seen.append((threading.get_ident(), devices))
        )
        wait(lambda: call.done)
        wait(lambda: bool(seen))
        assert seen[0][0] == main_thread
        assert [item.port for item in seen[0][1]] == ["ASYNC"]
        assert [item.port for item in call.result()] == ["ASYNC"]
        assert not call.cancelled and call.exception is None
        facade.disconnect()


def test_scan_async_result_waits_for_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject early result reads while the worker is still scanning."""
    app()
    backend = FakeBackend(
        [SimulatedDevice(None, None, port="ASYNC-WAIT", baudrate=115200)]
    )
    open_port = backend.open

    def open_later(
        port: str, params: Mapping[str, object]
    ) -> TransportProtocol:
        time.sleep(0.2)
        return open_port(port, params)

    monkeypatch.setattr(backend, "open", open_later)
    with use_fake_backend(backend):
        facade = SerialForge()
        call = facade.scan_async(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        with pytest.raises(CommandNotReadyError):
            call.result()
        wait(lambda: call.done, timeout=3)
        assert call.result() == []
        facade.disconnect()


def test_scan_async_failure_skips_callback_and_raises_from_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep success closures silent and retain the error for result()."""
    app()
    backend = FakeBackend([SimulatedDevice(None, None, port="ASYNC-FAIL")])
    with use_fake_backend(backend):
        facade = SerialForge()
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        core = facade._core
        assert core is not None

        def fail_disconnect() -> None:
            raise SerialForgeError("connection worker did not stop")

        with monkeypatch.context() as patch:
            patch.setattr(core, "disconnect", fail_disconnect)
            call = facade.scan_async()
            seen: list[list[DeviceInfo]] = []
            call.add_done_callback(seen.append)
            wait(lambda: call.done)
            assert seen == []
            assert isinstance(call.exception, SerialForgeError)
            with pytest.raises(SerialForgeError, match="did not stop"):
                call.result()
        facade.disconnect()


def test_scan_async_rejects_concurrent_lifecycle_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reserve the facade while the asynchronous scan is still running."""
    app()
    backend = FakeBackend(
        [SimulatedDevice(None, None, port="ASYNC-BUSY", baudrate=115200)]
    )
    open_port = backend.open

    def open_later(
        port: str, params: Mapping[str, object]
    ) -> TransportProtocol:
        time.sleep(0.3)
        return open_port(port, params)

    monkeypatch.setattr(backend, "open", open_later)
    with use_fake_backend(backend):
        facade = SerialForge()
        facade.configure(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        call = facade.scan_async()
        with pytest.raises(ConfigError):
            facade.scan_async()
        with pytest.raises(ConfigError):
            facade.scan_sync()
        with pytest.raises(ConfigError):
            facade.connect_sync()
        with pytest.raises(ConfigError):
            facade.configure(
                baudrates=[9600],
                probe="Probe",
                probe_pattern="READY",
            )
        call.cancel()
        wait(lambda: call.done, timeout=3)
        facade.disconnect()


def test_scan_async_cancel_suppresses_callback() -> None:
    """Stop the worker without running success closures."""
    app()
    backend = FakeBackend([SimulatedDevice(None, None, port="ASYNC-CANCEL")])
    with use_fake_backend(backend):
        facade = SerialForge()
        call = facade.scan_async(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        seen: list[list[DeviceInfo]] = []
        call.add_done_callback(seen.append)
        wait(lambda: facade._scanning, timeout=3)
        call.cancel()
        wait(lambda: call.done, timeout=3)
        assert call.cancelled
        assert seen == []
        assert call.result() == []
        facade.disconnect()


def test_disconnect_during_scan_async_returns_and_suppresses_callback() -> None:
    """Join the asynchronous scan from another thread without a deadlock."""
    app()
    backend = FakeBackend([SimulatedDevice(None, None, port="ASYNC-JOIN")])
    with use_fake_backend(backend):
        facade = SerialForge()
        call = facade.scan_async(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
        seen: list[list[DeviceInfo]] = []
        call.add_done_callback(seen.append)
        wait(lambda: facade._scanning, timeout=3)
        started = time.monotonic()
        facade.disconnect()
        assert time.monotonic() - started < 3
        wait(lambda: call.done)
        assert call.cancelled
        assert seen == []


def test_tagged_contexts_and_control_named_placeholder(
    device: SerialForge,
) -> None:
    """Keep concurrent calls isolated even when a wire field is named
    priority.
    """
    spec = device.add(
        "Read {priority}",
        r"VALUE:(?P<priority>\d+):(?P<value>\d+)",
        dict,
        correlation=Correlation.TAGGED,
    )
    first = device.send_async(spec, params={"priority": 1}, context={"row": 1})
    second = device.send_async(spec, params={"priority": 2}, context={"row": 2})
    wait(lambda: first.done and second.done)
    assert first.request_id != second.request_id
    assert first.result().context["row"] == 1
    assert second.result().context["row"] == 2
    assert first.result().data == [{"priority": "1", "value": "41"}]
    assert second.result().data == [{"priority": "2", "value": "42"}]


def test_callback_errors_use_host_boundary_and_continue(
    device: SerialForge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report a closure error without changing execution success or other
    closures.
    """
    errors: list[Exception] = []
    delivered: list[Any] = []
    monkeypatch.setattr(
        sys, "excepthook", lambda kind, error, trace: errors.append(error)
    )

    def fail(message: Message[Any]) -> None:
        raise ValueError("closure failed")

    call = device.send_async(device.add("Read", "VALUE:.*"))
    call.add_done_callback(fail).add_done_callback(delivered.append)
    wait(lambda: bool(delivered))
    assert call.result().ok
    assert len(errors) == 1 and call.callback_exception is errors[0]
