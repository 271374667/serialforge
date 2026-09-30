"""M1 placeholder for device discovery."""

from __future__ import annotations

# PySide6 exposes these C++ types through generated bindings, which pylint
# cannot resolve even though ty and Python imports can.
# pylint: disable=no-name-in-module
from PySide6.QtCore import QObject, Signal
# pylint: enable=no-name-in-module

from ..enums import ScanMode
from ..models import DeviceInfo, DeviceProfile


class DeviceFinder(QObject):
    """Expose the future scan, probe and baud-cache facade."""

    probe_progress = Signal(str, int)
    probe_finished = Signal(object)

    def __init__(self, profile: DeviceProfile) -> None:
        """Create a finder for ``profile``."""
        super().__init__()
        self._profile: DeviceProfile = profile

    def find(self, mode: ScanMode = ScanMode.ALL) -> list[DeviceInfo]:
        """Synchronously scan devices; implemented in M6."""
        del mode
        raise NotImplementedError("DeviceFinder.find is planned for M6")

    def find_async(self, mode: ScanMode = ScanMode.ALL) -> None:
        """Start an asynchronous scan; implemented in M6."""
        del mode
        raise NotImplementedError("DeviceFinder.find_async is planned for M6")

    def cancel(self) -> None:
        """Cancel an asynchronous scan; implemented in M6."""
        raise NotImplementedError("DeviceFinder.cancel is planned for M6")

    def forget(self, device: DeviceInfo) -> None:
        """Forget a cached device baudrate; implemented in M6."""
        del device
        raise NotImplementedError("DeviceFinder.forget is planned for M6")
