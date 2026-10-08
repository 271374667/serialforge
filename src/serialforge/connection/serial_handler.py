"""One-device QtCore facade for serial connection and command lifecycle."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import TYPE_CHECKING

# PySide6 exposes these C++ types through generated bindings, which pylint
# cannot resolve even though ty and Python imports can.
# pylint: disable=no-name-in-module
from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, Signal

# pylint: enable=no-name-in-module
from serialforge.advanced import CommandPriority, CommandTicket, HandlerStats
from serialforge.connection.command_registry import CommandRegistry
from serialforge.connection.connection_worker import ConnectionWorker
from serialforge.diagnostics import TrafficLogger
from serialforge.enums import CommandStatus, ConnectionState, SendRoute
from serialforge.errors import CommandError, SerialForgeError
from serialforge.models import (
    CommandResult,
    CommandSpec,
    DeviceInfo,
    DeviceProfile,
    EventSpec,
    LogConfig,
)

# The facade owns the complete lifecycle state for one serial connection.
# pylint: disable=too-many-instance-attributes

if TYPE_CHECKING:
    from serialforge.connection.command_dispatcher import CommandDispatcher


class SerialHandler(QObject):
    """Coordinate one device connection, commands and QtCore signals.

    Create and retain the handler in the application's Qt thread. Use QObject
    slots in that thread for queued delivery; keep its event loop running.
    ``send`` accepts calls from any thread. Connection methods belong to the
    owner thread. Declarations and results retain the original spec identity.

    Attributes:
        connection_state_changed: Emit the new ConnectionState.
        command_finished: Emit one CommandResult per accepted ticket.
        event_received: Emit a DeviceEvent for push, stream or unknown frames.
        traffic_logged: Emit a TrafficRecord when logging is enabled.
        error_occurred: Emit a SerialForgeError for asynchronous failures.

    Example:
        With a QCoreApplication and application-owned declarations already
        created, register before sending: ``handler.register(spec)``;
        ``handler.connect()``; after CONNECTED, ``handler.send(spec)``.
        In the completion slot, check ``result.spec is spec`` and ``result.ok``.
        See ``examples/demo.py`` for an executable no-hardware application.
    """

    connection_state_changed = Signal(ConnectionState)
    command_finished = Signal(object)
    event_received = Signal(object)
    traffic_logged = Signal(object)
    error_occurred = Signal(object)

    def __init__(
        self,
        profile: DeviceProfile,
        *,
        log: LogConfig | None = None,
        allow_raw_text: bool = True,
    ) -> None:
        """Create a disconnected handler with no open serial resources.

        Args:
            profile: Device filtering, probing, framing and connection policy.
            log: Optional logging policy; omitted means silent logging.
            allow_raw_text: Allow unmatched text sends when True (default).

        Raises:
            SerialForgeError: No QCoreApplication has been created.
        """
        application = QCoreApplication.instance()
        if application is None:
            raise SerialForgeError("SerialHandler requires a QCoreApplication")
        super().__init__()
        self._profile: DeviceProfile = profile
        self._log: LogConfig = log or LogConfig()
        self._allow_raw_text: bool = allow_raw_text
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._state_lock: threading.RLock = threading.RLock()
        self._worker: ConnectionWorker | None = None
        self._traffic: TrafficLogger = TrafficLogger(
            self._log, profile.runtime, encoding=profile.encoding
        )
        self._traffic.traffic_logged.connect(
            self.traffic_logged.emit, Qt.ConnectionType.DirectConnection
        )
        self._traffic.error_occurred.connect(
            self._report_error, Qt.ConnectionType.DirectConnection
        )
        self._registry: CommandRegistry = CommandRegistry(profile.terminator)
        # Keep the top-level package import free of advanced implementation.
        # pylint: disable=import-outside-toplevel
        from serialforge.connection.command_dispatcher import CommandDispatcher

        self._dispatcher: CommandDispatcher = CommandDispatcher(
            profile, self._registry, self._write
        )
        self._dispatcher.command_finished.connect(
            self.command_finished.emit, Qt.ConnectionType.DirectConnection
        )
        self._dispatcher.event_received.connect(
            self.event_received.emit, Qt.ConnectionType.DirectConnection
        )
        self._dispatcher.error_occurred.connect(
            self.error_occurred.emit, Qt.ConnectionType.DirectConnection
        )
        self._dispatcher.tx_written.connect(
            self._record_tx, Qt.ConnectionType.DirectConnection
        )
        self._init_sequence: tuple[CommandSpec | str, ...] = ()
        application.aboutToQuit.connect(self.disconnect)

    def _write(self, data: bytes) -> int:
        """Send bytes through the active connection worker."""
        worker = self._worker
        if worker is None:
            raise RuntimeError("serial transport is not connected")
        return worker.write(data)

    @property
    def state(self) -> ConnectionState:
        """Return the current connection state."""
        with self._state_lock:
            return self._state

    # ty: ignore[invalid-method-override, missing-override-decorator] --
    # QObject has an unrelated C++ signal-connect overload with the same name.
    def connect(self, target: str | DeviceInfo | None = None) -> None:
        """Start discovery or a direct connection, returning immediately.

        Args:
            target: None discovers matching VID/PID devices; a port name skips
                VID/PID filtering but probes its baudrate. DeviceInfo with a
                known baudrate opens directly. States and errors use signals.

        Raises:
            SerialForgeError: A connection or earlier worker is still active.
        """
        with self._state_lock:
            if self._state not in {
                ConnectionState.DISCONNECTED,
                ConnectionState.FAILED,
            }:
                raise SerialForgeError(
                    f"cannot connect while {self._state.value}"
                )
            worker = self._worker
            if worker is not None and worker.isRunning():
                raise SerialForgeError(
                    "previous connection worker is still running"
                )
            self._worker = ConnectionWorker(self, target)
        self._transition(
            ConnectionState.CONNECTING
            if isinstance(target, DeviceInfo)
            else ConnectionState.PROBING
        )
        assert self._worker is not None
        self._worker.start()

    # ty: ignore[invalid-method-override, missing-override-decorator] --
    # This public facade method intentionally shadows QObject.disconnect().
    def disconnect(self) -> None:
        """Stop and join I/O threads; repeated calls have no additional effect.

        Pending commands complete as CANCELLED. Explicit disconnection ends
        the log session and stops automatic reconnect attempts.

        Raises:
            SerialForgeError: The worker cannot stop within five seconds.
        """
        worker = self._worker
        if worker is not None:
            worker.request_stop()
            for _ in range(50):
                if worker.wait(100):
                    break
                worker.request_stop()
            else:
                raise SerialForgeError("connection worker did not stop")
            self._worker = None
        self._dispatcher.set_connected(False, user_initiated=True)
        self._traffic.end_connection()
        self._transition(ConnectionState.DISCONNECTED)

    def _transition(self, state: ConnectionState) -> None:
        """Publish a state change from either connection thread."""
        with self._state_lock:
            if self._state is state:
                return
            self._state = state
        self.connection_state_changed.emit(state)

    def _report_error(self, error: Exception) -> None:
        """Normalize I/O failures to the public exception hierarchy."""
        payload = (
            error
            if isinstance(error, SerialForgeError)
            else SerialForgeError(str(error))
        )
        self.error_occurred.emit(payload)

    def _receive_frame(self, frame: bytes) -> None:
        """Record and dispatch one completed receive frame."""
        worker = self._worker
        port = worker.port if worker is not None else None
        self._traffic.record_rx(port or "-", frame)
        self._dispatcher.receive_frame(frame)

    def _record_tx(
        self, data: bytes, route: SendRoute, spec: CommandSpec | None
    ) -> None:
        """Record a successful command write with its original spec."""
        worker = self._worker
        port = worker.port if worker is not None else None
        self._traffic.record_tx(port or "-", data, route, spec)

    def register(self, *specs: CommandSpec | EventSpec) -> None:
        """Register original declarations before sends and event matching.

        Args:
            specs: Frozen CommandSpec or EventSpec objects retained by identity.

        Raises:
            CommandError: Normalized requests or event patterns conflict.
        """
        self._registry.register(*specs)

    def unregister(self, spec: CommandSpec | EventSpec) -> None:
        """Remove a declaration from future sends and event matches.

        Args:
            spec: The exact registered object; existing invocations retain it.
        """
        self._registry.unregister(spec)

    def set_init_sequence(self, items: list[CommandSpec | str]) -> None:
        """Set the ordered commands run before CONNECTED and after reconnect.

        Args:
            items: Registered specs without placeholders or registered command
                text with parameters already rendered. An empty list clears it.

        Raises:
            CommandError: A step is unregistered or requires missing parameters.
        """
        for item in items:
            if isinstance(item, CommandSpec):
                if not self._registry.contains(item):
                    raise CommandError(
                        f"init command {item.name!r} is not registered"
                    )
                if item.placeholders:
                    raise CommandError(
                        "init command with placeholders requires text"
                    )
            elif isinstance(item, str):
                if (
                    self._registry.resolve_text(item, self._profile.terminator)
                    is None
                ):
                    raise CommandError(
                        f"init command text is not registered: {item!r}"
                    )
            else:
                raise CommandError("init sequence accepts CommandSpec or str")
        self._init_sequence = tuple(items)

    def send(
        self,
        target: CommandSpec | str,
        /,
        *,
        priority: CommandPriority | None = None,
        **params: object,
    ) -> CommandTicket:
        """Queue a registered command or safe raw text without blocking.

        Args:
            target: Registered spec or text matched against the registry.
            priority: Optional queue priority; None uses NORMAL.
            **params: Values for named request placeholders; text accepts none.

        Returns:
            A ticket carrying request_id, original spec and routing information.
            Completion arrives on command_finished, including DISCONNECTED or
            BUSY when a send cannot be accepted for transmission.

        Raises:
            CommandError: Spec is unregistered, parameters contain control
                characters, or unmatched text is disabled.
        """
        if priority is None:
            priority = CommandPriority.NORMAL
        return self._dispatcher.submit(
            target,
            priority=priority,
            allow_raw_text=self._allow_raw_text,
            **params,
        )

    def send_and_wait(
        self, target: CommandSpec | str, /, *, timeout: float, **params: object
    ) -> CommandResult:
        """Wait in a script thread that has no running Qt event loop.

        Args:
            target: Registered spec or text with the same semantics as send.
            timeout: Maximum wait in seconds; must be positive.
            **params: Named request values accepted by send.

        Returns:
            The terminal CommandResult, including TIMEOUT if the wait expires.

        Raises:
            CommandError: Called in a running Qt event loop or send is invalid.
        """
        if QThread.currentThread().loopLevel() > 0:
            raise CommandError("send_and_wait cannot run in a Qt event loop")
        if timeout <= 0:
            raise CommandError("timeout must be positive")
        received: list[CommandResult] = []
        done = threading.Event()
        request_id: str | None = None

        def on_finished(result: CommandResult) -> None:
            received.append(result)
            if result.request_id == request_id:
                done.set()

        self.command_finished.connect(
            on_finished, Qt.ConnectionType.DirectConnection
        )
        try:
            # ty: ignore[invalid-argument-type] -- send validates variadic parameters.
            ticket = self.send(target, **params)
            request_id = ticket.request_id
            if not any(
                result.request_id == ticket.request_id for result in received
            ):
                done.wait(timeout)
            for result in received:
                if result.request_id == ticket.request_id:
                    return result
            self._dispatcher.cancel_ticket(ticket, CommandStatus.TIMEOUT)
            for result in received:
                if result.request_id == ticket.request_id:
                    return result
            raise CommandError("command timeout could not be resolved")
        finally:
            self.command_finished.disconnect(on_finished)

    def set_log_config(self, config: LogConfig) -> None:
        """Apply logging switches during the connection's current session.

        Args:
            config: New logging policy. Disabling output does not add a sink.
        """
        self._log = config
        self._traffic.set_log_config(config)

    def stats(self) -> HandlerStats:
        """Return an immutable snapshot of latency and drop counters.

        Returns:
            HandlerStats; unobserved latency estimates remain None.
        """
        return replace(
            self._dispatcher.stats(),
            dropped_traffic_lines=self._traffic.dropped_traffic_lines,
        )
