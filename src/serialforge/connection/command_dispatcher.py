"""Queue serial commands and associate complete frames with invocations."""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

# pylint: disable=no-name-in-module
from PySide6.QtCore import QObject, Signal

from ..advanced import CommandTicket, HandlerStats, RuntimeConfig
from ..enums import (
    CommandPriority,
    CommandStatus,
    Correlation,
    ResponseMode,
    SendRoute,
    TimeoutPolicy,
)
from ..errors import CommandError
from ..models import CommandResult, CommandSpec, DeviceEvent, DeviceProfile
from ..transport import LatencyTracker
from .command_registry import CommandRegistry
from .pending_command import PendingCommand
from .response_parser import ResponseParser


class CommandDispatcher(QObject):
    """Run the M4 command state machine on a transport owner's thread.

    ``submit`` only enqueues and may be called from any thread. The owner calls
    ``pump`` and ``receive_frame`` serially after opening a transport. The
    owner is responsible for byte framing and for continuing to pump timeouts.
    """

    command_finished = Signal(object)
    event_received = Signal(object)
    error_occurred = Signal(object)
    tx_written = Signal(bytes, object, object)

    def __init__(
        self,
        profile: DeviceProfile,
        registry: CommandRegistry,
        write: Callable[[bytes], int],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind a profile, registry, and single transport write function."""
        super().__init__()
        self._profile: DeviceProfile = profile
        self._registry: CommandRegistry = registry
        self._write: Callable[[bytes], int] = write
        self._clock: Callable[[], float] = clock
        runtime = profile.runtime
        assert isinstance(runtime, RuntimeConfig)
        self._runtime: RuntimeConfig = runtime
        self._queue: list[PendingCommand] = []
        self._inflight: list[PendingCommand] = []
        self._streams: list[PendingCommand] = []
        self._sequence: int = 0
        self._connected: bool = False
        self._last_write_at: float | None = None
        self._drain_until: float = 0.0
        self._clear_input: Callable[[], None] | None = None
        self._latency: LatencyTracker = LatencyTracker()
        self._auto_rto: float = 1.0
        self._events: deque[DeviceEvent] = deque()
        self._last_event_emit_at: float = clock()
        self._dropped_events: int = 0
        # A lock keeps the snapshot and queue coherent across caller threads.
        self._lock: threading.RLock = threading.RLock()

    def set_connected(
        self, connected: bool, *, user_initiated: bool = False
    ) -> None:
        """Mark transport availability and finish outstanding work on loss."""
        with self._lock:
            self._connected = connected
            if not connected:
                status = (
                    CommandStatus.CANCELLED
                    if user_initiated
                    else CommandStatus.DISCONNECTED
                )
                for pending in tuple(self._queue + self._inflight + self._streams):
                    self._finish(pending, status)
                self._drain_until = 0.0
                self._latency.reset()
                self._auto_rto = 1.0

    def set_clear_input(self, clear_input: Callable[[], None]) -> None:
        """Provide the transport's input-buffer drain action."""
        self._clear_input = clear_input

    def cancel_ticket(
        self,
        ticket: CommandTicket,
        status: CommandStatus = CommandStatus.CANCELLED,
    ) -> None:
        """Finish one accepted ticket immediately and at most once."""
        with self._lock:
            for pending in self._queue + self._inflight + self._streams:
                if pending.ticket is ticket:
                    self._finish(pending, status)
                    return

    def submit(
        self,
        target: CommandSpec | str,
        *,
        priority: CommandPriority = CommandPriority.NORMAL,
        allow_raw_text: bool = True,
        **params: object,
    ) -> CommandTicket:
        """Validate and enqueue a command without performing an I/O write.

        Raises:
            CommandError: If the declaration, text, or parameters are invalid.
        """
        spec: CommandSpec | None
        route: SendRoute
        actual_params: Mapping[str, object]
        if isinstance(target, CommandSpec):
            if not self._registry.contains(target):
                raise CommandError(
                    f"命令 {target.name!r} 尚未注册，请先调用 handler.register"
                )
            spec = target
            route = SendRoute.SPEC
            actual_params = params
            sent = self.render_request(spec, params)
        elif isinstance(target, str):
            if params:
                raise CommandError("text command cannot also receive params")
            match = self._registry.resolve_text(
                target, self._profile.terminator
            )
            if match is None:
                if not allow_raw_text:
                    raise CommandError(f"unregistered command text: {target!r}")
                spec = None
                route = SendRoute.RAW
                actual_params = {}
                sent = self.render_raw(target)
            else:
                spec, actual_params = match
                route = SendRoute.MATCHED
                sent = self.render_request(spec, actual_params)
        else:
            raise CommandError("send target must be CommandSpec or str")
        if not isinstance(priority, CommandPriority):
            raise CommandError("priority must be CommandPriority")
        now = self._clock()
        ticket = CommandTicket(uuid.uuid4().hex, spec, route)
        with self._lock:
            self._sequence += 1
            pending = PendingCommand(
                ticket=ticket,
                spec=spec,
                route=route,
                params=actual_params,
                sent=sent,
                priority=priority,
                sequence=self._sequence,
                queued_at=now,
            )
            if not self._connected:
                self._finish(pending, CommandStatus.DISCONNECTED)
            elif (
                self._streams
                and spec is not None
                and spec.mode is not ResponseMode.NO_REPLY
                and spec.correlation is Correlation.EXCLUSIVE
            ):
                self._finish(pending, CommandStatus.BUSY)
            elif len(self._queue) >= self._runtime.queue_limit:
                self._finish(pending, CommandStatus.BUSY)
            else:
                self._queue.append(pending)
                self._queue.sort(
                    key=lambda item: (-item.priority, item.sequence)
                )
        return ticket

    def render_request(
        self, spec: CommandSpec, params: Mapping[str, object]
    ) -> bytes:
        """Render exactly one safe line from a registered declaration."""
        expected = set(spec.placeholders)
        if set(params) != expected:
            raise CommandError(
                f"request params must be {sorted(expected)}, got {sorted(params)}"
            )
        values: dict[str, str] = {}
        for name, value in params.items():
            rendered = str(value)
            CommandRegistry.validate_text(rendered)
            values[name] = rendered
        request = CommandRegistry.normalise(
            spec.request.format_map(values), self._profile.terminator
        )
        CommandRegistry.validate_text(request)
        return (request + self._profile.terminator).encode(
            self._profile.encoding
        )

    def render_raw(self, text: str) -> bytes:
        """Retain the caller's text while adding exactly one terminator."""
        request = text.rstrip()
        terminator = self._profile.terminator
        if request.endswith(terminator):
            request = request[: -len(terminator)]
        CommandRegistry.validate_text(request.strip())
        return (request + terminator).encode(self._profile.encoding)

    def pump(self) -> None:
        """Advance timeouts, cancellation, throttled events, and writes."""
        with self._lock:
            now = self._clock()
            for pending in tuple(self._queue + self._inflight + self._streams):
                if pending.ticket.cancelled:
                    self._finish(pending, CommandStatus.CANCELLED)
                    continue
                if pending.started_at is None or pending.spec is None:
                    continue
                spec = pending.spec
                elapsed = now - pending.started_at
                if spec.total_timeout_s is not None and elapsed >= spec.total_timeout_s:
                    self.timeout(pending, now)
                elif (
                    pending.first_frame_at is None
                    and elapsed >= pending.timeout_s
                    and spec.mode is not ResponseMode.STREAM
                ):
                    self.timeout(pending, now)
                elif (
                    spec.mode is ResponseMode.MULTI
                    and pending.last_frame_at is not None
                    and now - pending.last_frame_at >= spec.idle_timeout_s
                ):
                    self._finish(pending, CommandStatus.OK)
            if self._drain_until and now >= self._drain_until:
                self._drain_until = 0.0
                if self._clear_input is not None:
                    self._clear_input()
            interval = self._runtime.stream_emit_interval_ms / 1000
            if now - self._last_event_emit_at >= interval:
                self.flush_events()
            while self._connected and self._queue:
                if now < self._drain_until:
                    break
                if (
                    self._last_write_at is not None
                    and now - self._last_write_at
                    < self._runtime.min_command_interval_s
                ):
                    break
                pending = self._queue[0]
                spec = pending.spec
                needs_reply = (
                    spec is not None and spec.mode is not ResponseMode.NO_REPLY
                )
                if self._inflight and (
                    any(
                        item.spec is not None
                        and item.spec.correlation is Correlation.EXCLUSIVE
                        for item in self._inflight
                    )
                    or (needs_reply and spec.correlation is Correlation.EXCLUSIVE)
                    or pending.route is SendRoute.RAW
                ):
                    break
                self._queue.pop(0)
                self.start(pending, now)
                if pending.finished:
                    continue
                if needs_reply and spec.correlation is Correlation.EXCLUSIVE:
                    break

    def receive_frame(self, frame: bytes) -> None:
        """Associate a complete frame with an invocation or device event."""
        with self._lock:
            now = self._clock()
            if now < self._drain_until:
                return
            text = frame.decode(self._profile.encoding, errors="replace")
            if self._profile.echo and any(
                frame == item.sent.removesuffix(
                    self._profile.terminator.encode(self._profile.encoding)
                )
                for item in self._inflight + self._streams
            ):
                return
            for pending in tuple(self._inflight):
                spec = pending.spec
                assert spec is not None
                if spec.error_pattern is not None and re.fullmatch(
                    spec.error_pattern, text
                ):
                    pending.raw_frames.append(frame)
                    self._finish(
                        pending, CommandStatus.DEVICE_ERROR, error_message=text
                    )
                    return
                if spec.mode is ResponseMode.MULTI and spec.until is not None:
                    if re.fullmatch(spec.until, text):
                        pending.raw_frames.append(frame)
                        self._finish(pending, CommandStatus.OK)
                        return
                if spec.pattern is None:
                    continue
                matched, data = self.parse_command(spec, frame, pending)
                if pending.finished:
                    return
                if not matched:
                    continue
                pending.raw_frames.append(frame)
                pending.last_frame_at = now
                if pending.first_frame_at is None:
                    pending.first_frame_at = now
                pending.values.append(data)
                if spec.mode is ResponseMode.SINGLE or (
                    spec.mode is ResponseMode.MULTI
                    and spec.count is not None
                    and len(pending.values) >= spec.count
                ):
                    self._finish(pending, CommandStatus.OK)
                return
            for pending in tuple(self._streams):
                spec = pending.spec
                assert spec is not None
                if spec.pattern is None:
                    continue
                if spec.error_pattern is not None and re.fullmatch(
                    spec.error_pattern, text
                ):
                    pending.raw_frames.append(frame)
                    self._finish(
                        pending, CommandStatus.DEVICE_ERROR, error_message=text
                    )
                    return
                matched, data = self.parse_command(spec, frame, pending)
                if pending.finished:
                    return
                if matched:
                    pending.raw_frames.append(frame)
                    pending.last_frame_at = now
                    self.enqueue_event(
                        DeviceEvent(
                            spec=spec,
                            request_id=pending.ticket.request_id,
                            data=data,
                            raw_frame=frame,
                            timestamp=now,
                        )
                    )
                    return
            for event_spec in self._registry.events:
                try:
                    matched, data = ResponseParser.match(event_spec, text)
                except CommandError as exc:
                    self.error_occurred.emit(exc)
                    return
                if matched:
                    self.enqueue_event(
                        DeviceEvent(
                            spec=event_spec,
                            data=data,
                            raw_frame=frame,
                            timestamp=now,
                        )
                    )
                    return
            for pending in tuple(self._streams):
                spec = pending.spec
                assert spec is not None
                if spec.pattern is None:
                    pending.raw_frames.append(frame)
                    pending.last_frame_at = now
                    self.enqueue_event(
                        DeviceEvent(
                            spec=spec,
                            request_id=pending.ticket.request_id,
                            data=text,
                            raw_frame=frame,
                            timestamp=now,
                        )
                    )
                    return
            for pending in tuple(self._inflight):
                spec = pending.spec
                assert spec is not None
                if spec.mode is ResponseMode.MULTI and spec.pattern is None:
                    pending.raw_frames.append(frame)
                    pending.values.append(text)
                    pending.last_frame_at = now
                    if pending.first_frame_at is None:
                        pending.first_frame_at = now
                    if spec.count is not None and len(pending.values) >= spec.count:
                        self._finish(pending, CommandStatus.OK)
                    return
            self.enqueue_event(
                DeviceEvent(data=text, raw_frame=frame, timestamp=now)
            )

    def flush_events(self) -> None:
        """Emit bounded queued events on the owner thread."""
        with self._lock:
            while self._events:
                self.event_received.emit(self._events.popleft())
            self._last_event_emit_at = self._clock()

    def stats(self) -> HandlerStats:
        """Return latency and dropped-event counters."""
        latency = self._latency.stats()
        return HandlerStats(
            srtt_s=latency.srtt_s,
            rttvar_s=latency.rttvar_s,
            poll_interval_s=latency.poll_interval_s,
            rto_s=self._auto_rto,
            dropped_events=self._dropped_events,
        )

    def parse_command(
        self, spec: CommandSpec, frame: bytes, pending: PendingCommand
    ) -> tuple[bool, Any]:
        """Check correlation keys and turn parser errors into results."""
        text = frame.decode(self._profile.encoding, errors="replace")
        if spec.pattern is None:
            return (True, text) if spec.mode is ResponseMode.STREAM else (False, None)
        match = re.fullmatch(spec.pattern, text)
        if match is None:
            return False, None
        for key, value in match.groupdict().items():
            if key in pending.params and str(pending.params[key]) != value:
                return False, None
        try:
            return ResponseParser.match(spec, text)
        except CommandError as exc:
            pending.raw_frames.append(frame)
            self._finish(
                pending, CommandStatus.PARSE_ERROR, error_message=str(exc)
            )
            return False, None

    def start(self, pending: PendingCommand, now: float) -> None:
        """Write one invocation and mark its response deadline."""
        pending.started_at = now
        spec = pending.spec
        if spec is not None:
            if spec.timeout_s is TimeoutPolicy.AUTO:
                pending.timeout_s = self._auto_rto
            else:
                pending.timeout_s = float(spec.timeout_s)
        try:
            written = self._write(pending.sent)
        except (OSError, RuntimeError) as exc:
            self._finish(
                pending,
                CommandStatus.DISCONNECTED,
                error_message=str(exc),
            )
            return
        if written != len(pending.sent):
            self._finish(
                pending,
                CommandStatus.DISCONNECTED,
                error_message="serial write accepted fewer bytes than requested",
            )
            return
        self._last_write_at = now
        self.tx_written.emit(pending.sent, pending.route, spec)
        if spec is None or spec.mode is ResponseMode.NO_REPLY:
            self._finish(pending, CommandStatus.OK)
        elif spec.mode is ResponseMode.STREAM:
            self._streams.append(pending)
        else:
            self._inflight.append(pending)

    def timeout(self, pending: PendingCommand, now: float) -> None:
        """Finish an expired invocation and isolate late exclusive frames."""
        spec = pending.spec
        if spec is not None and spec.timeout_s is TimeoutPolicy.AUTO:
            self._auto_rto = min(
                self._runtime.rto_max_s, self._auto_rto * 2
            )
        if spec is not None and spec.correlation is Correlation.EXCLUSIVE:
            self._drain_until = now + self._runtime.post_timeout_drain_s
        self._finish(pending, CommandStatus.TIMEOUT)

    def _finish(
        self,
        pending: PendingCommand,
        status: CommandStatus,
        *,
        error_message: str | None = None,
    ) -> None:
        """Publish exactly one terminal result for an accepted ticket."""
        if pending.finished:
            return
        pending.finished = True
        for collection in (self._queue, self._inflight, self._streams):
            if pending in collection:
                collection.remove(pending)
        now = self._clock()
        if (
            status is CommandStatus.OK
            and pending.started_at is not None
            and pending.first_frame_at is not None
            and pending.spec is not None
            and pending.spec.mode is ResponseMode.SINGLE
        ):
            self._latency.observe(
                max(now - pending.started_at, 0.000001)
            )
            estimate = self._latency.stats().rto_s
            if estimate is not None:
                self._auto_rto = min(
                    self._runtime.rto_max_s,
                    max(self._runtime.rto_min_s, estimate),
                )
        data: Any = None
        if pending.values:
            if (
                pending.spec is not None
                and pending.spec.mode is ResponseMode.MULTI
            ):
                data = list(pending.values)
            else:
                data = pending.values[0]
        result = CommandResult(
            spec=pending.spec,
            route=pending.route,
            params=pending.params,
            sent=pending.sent if pending.started_at is not None else b"",
            status=status,
            data=data,
            raw_frames=tuple(pending.raw_frames),
            elapsed_s=(now - pending.started_at)
            if pending.started_at is not None
            else 0.0,
            request_id=pending.ticket.request_id,
            error_message=error_message,
        )
        self.command_finished.emit(result)

    def enqueue_event(self, event: DeviceEvent) -> None:
        """Retain bounded event traffic until the next emission interval."""
        if len(self._events) >= self._runtime.event_queue_high_water:
            for index, queued in enumerate(self._events):
                if isinstance(queued.spec, CommandSpec):
                    del self._events[index]
                    break
            else:
                self._events.popleft()
            self._dropped_events += 1
        self._events.append(event)


__all__ = ["CommandDispatcher"]
