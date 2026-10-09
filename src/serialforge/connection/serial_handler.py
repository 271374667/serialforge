"""Compatibility name for the unified serial facade."""

from typing import Any

from serialforge.connection.command_call import CommandCall
from serialforge.connection.serial_forge import SerialForge
from serialforge.enums import CommandPriority
from serialforge.message import Message
from serialforge.models import CommandSpec, EventSpec


class SerialHandler(SerialForge):
    """Bridge legacy method names to SerialForge, using its unified signals.

    Legacy completion/event/error/log signals are deliberately absent.
    Blocking failures now raise the same exceptions as send_sync.
    """

    def register(self, *specs: CommandSpec | EventSpec) -> None:
        """Register existing declarations without changing their identity."""
        # This compatibility method preserves the legacy atomic registration.
        # pylint: disable=protected-access
        with self._lock:
            self._registry.register(*specs)

    def unregister(self, spec: CommandSpec | EventSpec) -> None:
        """Remove a legacy declaration by identity."""
        self.remove(spec)

    def send(
        self,
        target: CommandSpec | str,
        /,
        *,
        priority: CommandPriority = CommandPriority.NORMAL,
        **params: object,
    ) -> CommandCall[Any]:
        """Bridge legacy placeholder keywords to explicit params."""
        return self.send_async(target, priority=priority, params=params)

    def send_and_wait(
        self, target: CommandSpec | str, /, *, timeout: float, **params: object
    ) -> Message[Any]:
        """Bridge legacy timeout naming; execution failure raises."""
        return self.send_sync(target, wait_timeout_s=timeout, params=params)


__all__ = ["SerialHandler"]
