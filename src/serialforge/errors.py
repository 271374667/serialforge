"""Exception hierarchy for serialforge."""

from __future__ import annotations


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


__all__ = [
    "SerialForgeError",
    "PortBusyError",
    "PortNotFoundError",
    "PermissionDeniedError",
    "ProbeError",
    "FramingError",
    "CommandError",
    "ConfigError",
]
