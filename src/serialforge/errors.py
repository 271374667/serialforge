"""Exception hierarchy for serialforge."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from serialforge.message import Message


def _without_tracebacks(error: Exception) -> Exception:
    """Retain failure causes without retaining worker stack object graphs."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        current.__traceback__ = None
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return error


class SerialForgeError(Exception):
    """Base class for all serialforge errors."""


class PortBusyError(SerialForgeError):
    """Raised when a serial port is already owned by this process."""


class PortNotFoundError(SerialForgeError):
    """Raised when a requested serial port cannot be found."""


class PermissionDeniedError(SerialForgeError):
    """Raised when Windows denies access to a serial port."""


class ProbeError(SerialForgeError):
    """Raised when device probing fails."""


class FramingError(SerialForgeError):
    """Raised when a frame cannot be split or validated."""


class CommandError(SerialForgeError):
    """Raised for invalid command declarations or usage."""


class ConfigError(SerialForgeError):
    """Raised for invalid device or transport configuration."""


class CommandExecutionError(CommandError):
    """Retain invocation metadata when command execution fails."""

    def __init__(
        self, message: Message[Any], cause: Exception | None = None
    ) -> None:
        """Attach the exact terminal result and optional originating error."""
        super().__init__(message.error_message or message.status.value)
        self.request_id = message.request_id
        self.spec = message.spec
        self.context = message.context
        self.status = message.status
        self.sent = message.sent
        self.raw_frames = message.raw_frames
        self.cause = cause


class CommandTimeoutError(CommandExecutionError):
    """Indicate that the protocol or invocation deadline expired."""


class CommandCancelledError(CommandExecutionError):
    """Indicate explicit cancellation."""


class CommandBusyError(CommandExecutionError):
    """Indicate queue or stream capacity exhaustion."""


class CommandDisconnectedError(CommandExecutionError):
    """Indicate unavailable or failed transport."""


class CommandParseError(CommandExecutionError):
    """Indicate invalid response data."""


class DeviceCommandError(CommandExecutionError):
    """Indicate an error response from the device."""


class CommandNotReadyError(CommandError):
    """Indicate that a nonblocking result query is premature."""


__all__ = [
    "SerialForgeError",
    "PortBusyError",
    "PortNotFoundError",
    "PermissionDeniedError",
    "ProbeError",
    "FramingError",
    "CommandError",
    "ConfigError",
    "CommandExecutionError",
    "CommandTimeoutError",
    "CommandCancelledError",
    "CommandBusyError",
    "CommandDisconnectedError",
    "CommandParseError",
    "DeviceCommandError",
    "CommandNotReadyError",
]
