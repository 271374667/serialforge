"""Validate the public facade using deterministic serial substitutes."""

from __future__ import annotations

import gc
import sys
import threading
import time
import weakref
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from serialforge import (
    Correlation,
    Message,
    MessageCategory,
    ResponseMode,
    SerialForge,
    SerialHandler,
    Spec,
)
from serialforge.advanced import SerialConfig
from serialforge.enums import SpecRole
from serialforge.errors import (
    CommandCancelledError,
    CommandDisconnectedError,
    CommandError,
    CommandNotReadyError,
    CommandParseError,
    CommandTimeoutError,
    ConfigError,
    ProbeError,
)
from serialforge.qt_core import QCoreApplication, QThread
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
                    b"Read 1\r\n": b"VALUE:1:41\r\n",
                    b"Read 2\r\n": b"VALUE:2:42\r\n",
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
        facade.connect("FACADE")
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
    device.connect("FACADE")
    assert device.send_sync(spec).spec is spec
    device.remove(spec)
    with pytest.raises(CommandError):
        device.send_async(spec)


def test_sync_response_and_exact_signal_identity(device: SerialForge) -> None:
    """Return the exact same terminal object later observed on received."""
    observed: list[Message[Any]] = []
    device.received.connect(observed.append)
    spec = device.add("Read", r"VALUE:(?P<value>\d+)")
    result = device.send_sync(spec, context={"widget": "row"})
    assert result.data == "VALUE:42" and result.spec is spec
    wait(lambda: any(item is result for item in observed))
    assert result.context == {"widget": "row"}
    assert len([item for item in observed if item is result]) == 1
    assert any(
        item.category is MessageCategory.RESPONSE_FRAME for item in observed
    )
    assert (
        b"".join(
            item.raw_data
            for item in observed
            if item.category is MessageCategory.RAW_RECEIVE
        )
        == b"VALUE:42\r\n"
    )


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
    assert call.result().data == {"value": "42"}
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
        found = facade.scan(
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


def test_multi_parser_and_no_reply(device: SerialForge) -> None:
    """Parse named groups explicitly and collect counted responses."""
    multi = device.add(
        "Many", r"VALUE:(?P<value>\d+)", dict, mode=ResponseMode.MULTI, count=2
    )
    assert device.send_sync(multi).data == [{"value": "1"}, {"value": "2"}]
    stop = device.add("Stop", mode=ResponseMode.NO_REPLY)
    result = device.send_sync(stop)
    assert result.ok and result.data is None and result.sent == b"Stop\r\n"


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


def test_context_is_shared_with_each_response(device: SerialForge) -> None:
    """Reuse exactly one owned snapshot for handle, frames and terminal
    result.
    """
    observed: list[Message[Any]] = []
    device.received.connect(observed.append)
    spec = device.add(
        "Many", r"VALUE:(?P<value>\d+)", dict, mode=ResponseMode.MULTI, count=2
    )
    call = device.send_async(spec, context={"row": 5})
    wait(
        lambda: any(
            item.category is MessageCategory.COMMAND_RESULT for item in observed
        )
    )
    owned = [item for item in observed if item.request_id == call.request_id]
    assert len(owned) == 3
    assert all(item.context is call.context for item in owned)


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


def test_scan_io_fault_raises_and_closes_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise a probe fault instead of converting backend failure into no
    match.
    """
    app()
    backend = FakeBackend([SimulatedDevice(None, None, port="FAULT")])

    def fail(port: str, params: object) -> None:
        raise OSError("open failed")

    monkeypatch.setattr(backend, "open", fail)
    facade = SerialForge()
    with (
        use_fake_backend(backend),
        pytest.raises(ProbeError, match="open failed"),
    ):
        facade.scan(
            baudrates=[115200],
            probe="Probe",
            probe_pattern="READY",
            serial=SerialConfig(settle_time_s=0),
        )
    assert facade._scan_done.is_set()
    facade.disconnect()


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
    assert first.result().data == {"priority": "1", "value": "41"}
    assert second.result().data == {"priority": "2", "value": "42"}


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
