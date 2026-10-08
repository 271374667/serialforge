"""QtCore worker for a single asynchronous device discovery request."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

# pylint: disable=no-name-in-module
from PySide6.QtCore import QThread

from ..enums import ScanMode

# A single-purpose Qt worker intentionally exposes only ``run``.
# pylint: disable=too-few-public-methods

if TYPE_CHECKING:
    from .device_finder import DeviceFinder


class AsyncScanThread(QThread):
    """Run one scan without enumerating ports on the application thread."""

    def __init__(
        self, finder: DeviceFinder, mode: ScanMode, port: str | None
    ) -> None:
        """Bind an asynchronous scan request to its finder."""
        super().__init__()
        self._finder: DeviceFinder = finder
        self._mode: ScanMode = mode
        self._port: str | None = port

    @override
    def run(self) -> None:
        """Execute and publish the scan result."""
        self._finder.run_find(self._mode, self._port)


__all__ = ["AsyncScanThread"]
