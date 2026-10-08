"""One-device facade for registration and command submission."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

# PySide6 exposes these C++ types through generated bindings, which pylint
# cannot resolve even though ty and Python imports can.
# pylint: disable=no-name-in-module
from PySide6.QtCore import QObject, Qt, QThread, Signal

# pylint: enable=no-name-in-module
from ..enums import CommandStatus, ConnectionState
from ..errors import CommandError
from ..models import (
    CommandResult,
    CommandSpec,
    DeviceInfo,
    DeviceProfile,
    EventSpec,
)
from .command_registry import CommandRegistry

if TYPE_CHECKING:
    from ..advanced import CommandPriority, CommandTicket
    from .command_dispatcher import CommandDispatcher


class SerialHandler(QObject):
    """Expose the future one-device connection facade.

    M1 defines the public shape only. Transport, dispatch and lifecycle
    behavior are implemented in M2-M5.
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
        log: Any = None,
        allow_raw_text: bool = True,
    ) -> None:
        """Create a not-yet-connected handler for ``profile``."""
        super().__init__()
        self._profile: DeviceProfile = profile
        self._log: Any = log
        self._allow_raw_text: bool = allow_raw_text
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._registry: CommandRegistry = CommandRegistry(profile.terminator)
        # Keep the top-level package import free of advanced implementation.
        # pylint: disable=import-outside-toplevel
        from .command_dispatcher import CommandDispatcher

        self._dispatcher: CommandDispatcher = CommandDispatcher(
            profile, self._registry, self._write_unavailable
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
        self._init_sequence: tuple[CommandSpec | str, ...] = ()

    @staticmethod
    def _write_unavailable(data: bytes) -> int:
        """Fail if a disconnected facade is accidentally asked to write."""
        del data
        raise RuntimeError("serial transport is not connected")

    @property
    def state(self) -> ConnectionState:
        """Return the current connection state."""
        return self._state

    # ty: ignore[invalid-method-override, missing-override-decorator] --
    # QObject has an unrelated C++ signal-connect overload with the same name.
    def connect(self, target: str | DeviceInfo | None = None) -> None:
        """Connect to a device; implemented after the M1 skeleton."""
        del target
        raise NotImplementedError("SerialHandler.connect is planned for M5")

    # ty: ignore[invalid-method-override, missing-override-decorator] --
    # This public facade method intentionally shadows QObject.disconnect().
    def disconnect(self) -> None:
        """Disconnect from the device; implemented after the M1 skeleton."""
        raise NotImplementedError("SerialHandler.disconnect is planned for M5")

    def register(self, *specs: CommandSpec | EventSpec) -> None:
        """Register command and event declarations by object identity."""
        self._registry.register(*specs)

    def unregister(self, spec: CommandSpec | EventSpec) -> None:
        """Remove a declaration from future sends and event matches."""
        self._registry.unregister(spec)

    def set_init_sequence(self, items: list[CommandSpec | str]) -> None:
        """Validate and retain steps for M5 connection initialization."""
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
        """Queue a registered command or safe raw text without blocking."""
        if priority is None:
            # pylint: disable=import-outside-toplevel
            from ..advanced import CommandPriority

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
        """Wait in a script thread that has no running Qt event loop."""
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

    def set_log_config(self, config: Any) -> None:
        """Set logging configuration; implemented in M3."""
        del config
        raise NotImplementedError(
            "SerialHandler.set_log_config is planned for M3"
        )

    def stats(self) -> Any:
        """Return handler statistics; implemented in M2/M5."""
        raise NotImplementedError("SerialHandler.stats is planned for M2")
