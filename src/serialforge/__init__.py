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

from serialforge.connection import SerialHandler
from serialforge.discovery import DeviceFinder
from serialforge.enums import (
    CommandStatus,
    ConnectionState,
    Correlation,
    ResponseMode,
    ScanMode,
)
from serialforge.errors import SerialForgeError
from serialforge.models import (
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
