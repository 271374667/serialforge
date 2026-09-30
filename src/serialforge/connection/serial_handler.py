"""M1 placeholder for the connection facade."""

from __future__ import annotations

from typing import Any

# PySide6 exposes these C++ types through generated bindings, which pylint
# cannot resolve even though ty and Python imports can.
# pylint: disable=no-name-in-module
from PySide6.QtCore import QObject, Signal
# pylint: enable=no-name-in-module

from ..enums import ConnectionState
from ..models import (
    CommandResult,
    CommandSpec,
    DeviceInfo,
    DeviceProfile,
    EventSpec,
)


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
        """Register command declarations; implemented in M4."""
        del specs
        raise NotImplementedError("SerialHandler.register is planned for M4")

    def unregister(self, spec: CommandSpec | EventSpec) -> None:
        """Unregister one declaration; implemented in M4."""
        del spec
        raise NotImplementedError("SerialHandler.unregister is planned for M4")

    def set_init_sequence(self, items: list[CommandSpec | str]) -> None:
        """Set connection initialization commands; implemented in M4."""
        del items
        raise NotImplementedError(
            "SerialHandler.set_init_sequence is planned for M4"
        )

    def send(self, target: CommandSpec | str, /, **params: object) -> Any:
        """Queue a command; implemented in M4."""
        del target, params
        raise NotImplementedError("SerialHandler.send is planned for M4")

    def send_and_wait(
        self, target: CommandSpec | str, /, *, timeout: float, **params: object
    ) -> CommandResult:
        """Wait for a command result; implemented in M4."""
        del target, timeout, params
        raise NotImplementedError(
            "SerialHandler.send_and_wait is planned for M4"
        )

    def set_log_config(self, config: Any) -> None:
        """Set logging configuration; implemented in M3."""
        del config
        raise NotImplementedError(
            "SerialHandler.set_log_config is planned for M3"
        )

    def stats(self) -> Any:
        """Return handler statistics; implemented in M2/M5."""
        raise NotImplementedError("SerialHandler.stats is planned for M2")
