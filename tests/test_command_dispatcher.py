"""No-hardware tests for command scheduling and response association."""

from dataclasses import dataclass

import pytest

from serialforge import (
    CommandSpec,
    CommandStatus,
    Correlation,
    DeviceProfile,
    EventSpec,
    ResponseMode,
)
from serialforge.advanced import CommandPriority, RuntimeConfig, TimeoutPolicy
from serialforge.connection.command_dispatcher import CommandDispatcher
from serialforge.connection.command_registry import CommandRegistry
from serialforge.errors import CommandError


@dataclass(frozen=True)
class VersionInfo:
    """Hold the variable numeric suffix in a real device version reply."""

    version: str


VERSION = CommandSpec(
    "Version", r"Software version (?P<version>\d+\.\d+)", VersionInfo
)


def make_dispatcher(
    *,
    runtime: RuntimeConfig | None = None,
    echo: bool = False,
) -> tuple[CommandDispatcher, CommandRegistry, list[bytes], list[object], list[object], list[float]]:
    """Create a connected deterministic dispatcher with fake writes."""
    now = [0.0]
    writes: list[bytes] = []
    results: list[object] = []
    events: list[object] = []
    registry = CommandRegistry()
    profile = DeviceProfile(
        vid_pid=[(0x067B, 0x23A3)],
        baudrates=[115200],
        probe=VERSION,
        echo=echo,
        runtime=runtime,
    )

    def write(data: bytes) -> int:
        writes.append(data)
        return len(data)

    dispatcher = CommandDispatcher(
        profile, registry, write, clock=lambda: now[0]
    )
    dispatcher.command_finished.connect(results.append)
    dispatcher.event_received.connect(events.append)
    dispatcher.set_connected(True)
    return dispatcher, registry, writes, results, events, now


@pytest.mark.parametrize("version", ["1.02", "1.75", "2.10"])
def test_real_version_reply_without_terminator(version: str) -> None:
    """Parse the COM11 reply shape after request framing is applied."""
    dispatcher, registry, writes, results, _, _ = make_dispatcher()
    registry.register(VERSION)
    ticket = dispatcher.submit("Version\r\n")
    dispatcher.pump()
    assert writes == [b"Version\r\n"]
    dispatcher.receive_frame(f"Software version {version}".encode())
    assert len(results) == 1
    result = results[0]
    assert result.status is CommandStatus.OK
    assert result.data == VersionInfo(version)
    assert result.spec is VERSION
    assert result.request_id == ticket.request_id


def test_no_reply_raw_and_unregistered_spec() -> None:
    """Complete no-reply sends once while failing unknown objects early."""
    dispatcher, registry, writes, results, _, _ = make_dispatcher()
    reboot = CommandSpec("Reboot")
    with pytest.raises(CommandError, match="尚未注册"):
        dispatcher.submit(reboot)
    assert not results
    registry.register(reboot)
    dispatcher.submit(reboot)
    dispatcher.submit("Other\r\n")
    dispatcher.pump()
    assert writes == [b"Reboot\r\n", b"Other\r\n"]
    assert [result.status for result in results] == [
        CommandStatus.OK,
        CommandStatus.OK,
    ]
    assert results[0].spec is reboot
    assert results[1].spec is None
    assert results[1].sent == b"Other\r\n"


def test_template_parameters_and_injection_are_checked_before_queue() -> None:
    """Reject a control character or missing parameter without writing."""
    dispatcher, registry, writes, _, _, _ = make_dispatcher()
    set_mode = CommandSpec("SetMode {mode}")
    registry.register(set_mode)
    with pytest.raises(CommandError, match="control"):
        dispatcher.submit(set_mode, mode="run\r\nReboot")
    with pytest.raises(CommandError, match="params"):
        dispatcher.submit(set_mode)
    with pytest.raises(CommandError, match="control"):
        dispatcher.submit("Version\r\nReboot")
    dispatcher.submit("SetMode run")
    dispatcher.pump()
    assert writes == [b"SetMode run\r\n"]


def test_tagged_concurrency_and_exclusive_barrier() -> None:
    """Match tags across concurrent requests and hold an exclusive follower."""
    dispatcher, registry, writes, results, _, _ = make_dispatcher()
    ping = CommandSpec(
        "Ping {id}",
        r"Pong (?P<id>\d+)",
        correlation=Correlation.TAGGED,
    )
    registry.register(ping, VERSION)
    first = dispatcher.submit(ping, id=1)
    second = dispatcher.submit(ping, id=2)
    exclusive = dispatcher.submit(VERSION)
    dispatcher.pump()
    assert writes == [b"Ping 1\r\n", b"Ping 2\r\n"]
    dispatcher.receive_frame(b"Pong 2")
    dispatcher.pump()
    assert len(writes) == 2
    dispatcher.receive_frame(b"Pong 1")
    dispatcher.pump()
    assert writes[-1] == b"Version\r\n"
    dispatcher.receive_frame(b"Software version 1.75")
    assert [item.request_id for item in results] == [
        second.request_id,
        first.request_id,
        exclusive.request_id,
    ]


def test_multi_count_until_and_unsolicited_event() -> None:
    """End a multi response by count or a marker and route unrelated frames."""
    dispatcher, registry, _, results, events, _ = make_dispatcher()
    multi = CommandSpec("ReadAll", r"VALUE (?P<value>\d+)", count=2)
    until = CommandSpec("Dump", until=r"END")
    proactive = EventSpec(r"EVT (?P<state>ON|OFF)")
    registry.register(multi, until, proactive)
    dispatcher.submit(multi)
    dispatcher.pump()
    dispatcher.receive_frame(b"EVT ON")
    dispatcher.receive_frame(b"VALUE 1")
    dispatcher.receive_frame(b"VALUE 2")
    dispatcher.pump()
    dispatcher.submit(until)
    dispatcher.pump()
    dispatcher.receive_frame(b"alpha")
    dispatcher.receive_frame(b"END")
    dispatcher.flush_events()
    assert results[0].data == [{"value": "1"}, {"value": "2"}]
    assert results[1].data == ["alpha"]
    assert len(events) == 1
    assert events[0].spec is proactive


def test_timeout_drain_and_ticket_cancellation_are_exactly_once() -> None:
    """Keep a late reply away from the following exclusive request."""
    dispatcher, registry, writes, results, _, now = make_dispatcher(
        runtime=RuntimeConfig(post_timeout_drain_s=0.2)
    )
    registry.register(VERSION)
    first = dispatcher.submit(VERSION)
    second = dispatcher.submit(VERSION)
    dispatcher.pump()
    now[0] = 1.01
    dispatcher.pump()
    assert results[0].request_id == first.request_id
    assert results[0].status is CommandStatus.TIMEOUT
    dispatcher.receive_frame(b"Software version 1.02")
    now[0] = 1.22
    dispatcher.pump()
    assert len(writes) == 2
    dispatcher.receive_frame(b"Software version 1.75")
    assert results[1].request_id == second.request_id
    assert results[1].data == VersionInfo("1.75")
    dispatcher.pump()
    assert len(results) == 2


def test_stream_cancel_and_backpressure() -> None:
    """Stream frames carry the command identity and bound queued events."""
    dispatcher, registry, writes, results, events, _ = make_dispatcher(
        runtime=RuntimeConfig(event_queue_high_water=2)
    )
    stream = CommandSpec(
        "Monitor", r"ADC (?P<value>\d+)", mode=ResponseMode.STREAM
    )
    stop = CommandSpec("Stop")
    registry.register(stream, stop)
    ticket = dispatcher.submit(stream)
    dispatcher.pump()
    dispatcher.receive_frame(b"ADC 1")
    dispatcher.receive_frame(b"ADC 2")
    dispatcher.receive_frame(b"ADC 3")
    dispatcher.submit(stop, priority=CommandPriority.URGENT)
    dispatcher.pump()
    assert writes == [b"Monitor\r\n", b"Stop\r\n"]
    assert dispatcher.stats().dropped_events == 1
    dispatcher.flush_events()
    assert [item.data for item in events] == [
        {"value": "2"},
        {"value": "3"},
    ]
    assert all(item.spec is stream for item in events)
    ticket.cancel()
    dispatcher.pump()
    assert results[-1].status is CommandStatus.CANCELLED
    assert results[-1].spec is stream


def test_auto_timeout_and_echo_skip() -> None:
    """Apply AUTO RTO, skip an echoed request, and parse the response."""
    dispatcher, registry, _, results, _, now = make_dispatcher(echo=True)
    auto = CommandSpec(
        "Version",
        r"Software version (?P<version>\d+\.\d+)",
        timeout_s=TimeoutPolicy.AUTO,
    )
    registry.register(auto)
    dispatcher.submit(auto)
    dispatcher.pump()
    dispatcher.receive_frame(b"Version")
    assert not results
    now[0] = 0.1
    dispatcher.receive_frame(b"Software version 1.02")
    assert results[0].status is CommandStatus.OK
    assert dispatcher.stats().rto_s is not None


def test_device_error_parse_error_and_tx_annotation_once() -> None:
    """Report device and parsing failures and annotate one actual TX each."""
    dispatcher, registry, _, results, _, _ = make_dispatcher()
    command = CommandSpec(
        "Read",
        r"VALUE (?P<value>\d+)",
        int,
        error_pattern=r"ERROR .*",
    )
    registry.register(command)
    tx: list[tuple[bytes, object, object]] = []
    dispatcher.tx_written.connect(
        lambda data, route, spec: tx.append((data, route, spec))
    )
    dispatcher.submit(command)
    dispatcher.pump()
    dispatcher.receive_frame(b"ERROR busy")
    assert results[-1].status is CommandStatus.DEVICE_ERROR
    dispatcher.submit("Read")
    dispatcher.pump()
    dispatcher.receive_frame(b"VALUE 8")
    assert results[-1].status is CommandStatus.OK
    assert results[-1].data == 8
    assert len(tx) == 2
    assert tx[0][2] is command and tx[1][2] is command
    assert tx[0][1].name == "SPEC"
    assert tx[1][1].name == "MATCHED"


def test_queue_priority_limit_interval_and_cancel() -> None:
    """Bound queued work, send high priority first, and enforce spacing."""
    dispatcher, registry, writes, results, _, now = make_dispatcher(
        runtime=RuntimeConfig(queue_limit=2, min_command_interval_s=0.05)
    )
    command = CommandSpec("Nop")
    registry.register(command)
    low = dispatcher.submit(command)
    high = dispatcher.submit(command, priority=CommandPriority.HIGH)
    overflow = dispatcher.submit(command)
    assert results[0].request_id == overflow.request_id
    assert results[0].status is CommandStatus.BUSY
    dispatcher.pump()
    assert len(writes) == 1
    assert results[1].request_id == high.request_id
    low.cancel()
    now[0] = 0.01
    dispatcher.pump()
    assert results[2].request_id == low.request_id
    assert results[2].status is CommandStatus.CANCELLED
    dispatcher.pump()
    assert len(results) == 3


def test_multi_idle_and_auto_rto_backoff() -> None:
    """Idle-complete partial MULTI data and double AUTO RTO on timeout."""
    dispatcher, registry, _, results, _, now = make_dispatcher()
    multi = CommandSpec(
        "Read", r"VALUE (?P<value>\d+)", mode=ResponseMode.MULTI,
        idle_timeout_s=0.2,
    )
    auto = CommandSpec("Ping", r"Pong", timeout_s=TimeoutPolicy.AUTO)
    registry.register(multi, auto)
    dispatcher.submit(multi)
    dispatcher.pump()
    dispatcher.receive_frame(b"VALUE 5")
    now[0] = 0.21
    dispatcher.pump()
    assert results[0].data == [{"value": "5"}]
    dispatcher.submit(auto)
    dispatcher.pump()
    now[0] = 1.22
    dispatcher.pump()
    assert results[1].status is CommandStatus.TIMEOUT
    assert dispatcher.stats().rto_s == 2.0


def test_stream_keeps_proactive_event_when_buffer_is_full() -> None:
    """Frequent stream frames may not evict a low-frequency device event."""
    dispatcher, registry, _, _, events, _ = make_dispatcher(
        runtime=RuntimeConfig(event_queue_high_water=2)
    )
    stream = CommandSpec("Monitor", mode=ResponseMode.STREAM)
    proactive = EventSpec(r"EVT (?P<state>ON|OFF)")
    registry.register(stream, proactive)
    dispatcher.submit(stream)
    dispatcher.pump()
    dispatcher.receive_frame(b"RAW 1")
    dispatcher.receive_frame(b"EVT ON")
    dispatcher.receive_frame(b"RAW 2")
    dispatcher.flush_events()
    assert len(events) == 2
    assert events[0].spec is proactive
    assert events[1].spec is stream
