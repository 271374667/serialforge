"""Public serialforge API.

Importing this module only defines types and Qt signals.  It does not create a
Qt application, open a port, start a thread, create a file or configure a
loguru sink.
"""

# Re-export lists are intentionally repeated at each public namespace boundary.
# pylint: disable=duplicate-code

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("serialforge")
except PackageNotFoundError:
    __version__ = "0.0.1"

from .connection import SerialHandler
from .discovery import DeviceFinder
from .enums import (
    CommandStatus,
    ConnectionState,
    Correlation,
    ResponseMode,
    ScanMode,
)
from .errors import SerialForgeError
from .models import (
    CommandResult,
    CommandSpec,
    DeviceEvent,
    DeviceInfo,
    DeviceProfile,
    EventSpec,
    LogConfig,
)

__all__ = [
    "SerialHandler",
    "DeviceFinder",
    "DeviceProfile",
    "CommandSpec",
    "EventSpec",
    "LogConfig",
    "CommandResult",
    "DeviceEvent",
    "DeviceInfo",
    "CommandStatus",
    "ConnectionState",
    "ResponseMode",
    "Correlation",
    "ScanMode",
    "SerialForgeError",
]
