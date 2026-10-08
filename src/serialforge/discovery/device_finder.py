"""QtCore facade for bounded serial discovery and baudrate probing."""

from __future__ import annotations

import threading
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
    as_completed,
)

from loguru import logger

# pylint: disable=no-name-in-module
from PySide6.QtCore import QCoreApplication, QObject, Signal

from ..advanced import RuntimeConfig
from ..enums import ScanMode
from ..errors import ProbeError, SerialForgeError
from ..models import DeviceInfo, DeviceProfile
from ..protocols import PortBackend
from ..settings import LOG_ENABLED
from ..transport import BackendSwitch, SerialTransport
from .async_scan_thread import AsyncScanThread
from .baud_cache import BaudCache
from .baud_prober import BaudProber
from .port_scanner import PortScanner

# The finder coordinates cache, cancellation, and Qt worker ownership.
# pylint: disable=too-many-instance-attributes


class DeviceFinder(QObject):
    """Find matching serial devices and remember their working baudrates."""

    probe_progress = Signal(str, int)
    probe_finished = Signal(object)

    def __init__(self, profile: DeviceProfile) -> None:
        """Create a finder without enumerating or opening any port."""
        application = QCoreApplication.instance()
        if application is None:
            raise SerialForgeError("DeviceFinder requires a QCoreApplication")
        super().__init__()
        self._profile: DeviceProfile = profile
        self._backend: PortBackend = BackendSwitch.current()
        self._cache: BaudCache = (
            BaudCache.application_cache()
            if isinstance(self._backend, SerialTransport)
            else BaudCache()
        )
        self._cancel: threading.Event = threading.Event()
        self._lock: threading.RLock = threading.RLock()
        self._active: bool = False
        self._thread: AsyncScanThread | None = None
        self._shutdown_hooked: bool = False

    def find(
        self, mode: ScanMode = ScanMode.ALL, *, port: str | None = None
    ) -> list[DeviceInfo]:
        """Scan synchronously while keeping enumeration on a worker thread."""
        self._begin()
        return self.run_find(mode, port)

    def find_async(
        self, mode: ScanMode = ScanMode.ALL, *, port: str | None = None
    ) -> None:
        """Start one background scan and publish its eventual result."""
        self._begin()
        thread = AsyncScanThread(self, mode, port)
        self._thread = thread
        application = QCoreApplication.instance()
        assert application is not None
        application.aboutToQuit.connect(self._shutdown)
        self._shutdown_hooked = True
        thread.start()

    def cancel(self) -> None:
        """Request cooperative cancellation of an active scan."""
        self._cancel.set()

    def _shutdown(self) -> None:
        """Join an asynchronous scan before Qt destroys its thread."""
        self.cancel()
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.wait(5000)

    def forget(self, device: DeviceInfo) -> None:
        """Discard one remembered baudrate hint."""
        self._cache.forget(device)

    def _begin(self) -> None:
        """Reject overlapping scans and reset cancellation state."""
        with self._lock:
            if self._active:
                raise ProbeError("a device scan is already active")
            self._active = True
            self._cancel.clear()

    def run_find(self, mode: ScanMode, port: str | None) -> list[DeviceInfo]:
        """Run the coordinator and publish exactly one completion signal."""
        result: list[DeviceInfo] = []
        try:
            with ThreadPoolExecutor(max_workers=1) as coordinator:
                result = coordinator.submit(self._scan, mode, port).result()
        except (OSError, RuntimeError) as exc:
            if LOG_ENABLED:
                logger.debug("device scan failed: {}", exc)
        finally:
            with self._lock:
                self._active = False
            if self._shutdown_hooked:
                application = QCoreApplication.instance()
                if application is not None:
                    application.aboutToQuit.disconnect(self._shutdown)
                self._shutdown_hooked = False
            self.probe_finished.emit(result)
        return result

    def _scan(self, mode: ScanMode, port: str | None) -> list[DeviceInfo]:
        """Probe separate ports concurrently and stop on the first match."""
        runtime = self._profile.runtime
        assert isinstance(runtime, RuntimeConfig)
        deadline = time.monotonic() + runtime.probe_total_timeout_s
        candidates = PortScanner(self._profile, self._backend).scan(port)
        prober = BaudProber(self._profile, self._backend, self._cache)
        results: list[DeviceInfo] = []
        with ThreadPoolExecutor(max_workers=runtime.probe_pool_size) as pool:
            futures = [
                pool.submit(
                    prober.probe,
                    candidate,
                    self._cancel,
                    deadline,
                    self.probe_progress.emit,
                )
                for candidate in candidates
            ]
            try:
                for future in as_completed(
                    futures, timeout=max(0.001, deadline - time.monotonic())
                ):
                    try:
                        device = future.result()
                    except (OSError, RuntimeError) as exc:
                        if LOG_ENABLED:
                            logger.debug("device probe worker failed: {}", exc)
                        continue
                    if device is not None:
                        results.append(device)
                        if mode is ScanMode.FIRST_MATCH:
                            self._cancel.set()
                            break
            except FutureTimeoutError:
                self._cancel.set()
        return results[:1] if mode is ScanMode.FIRST_MATCH else results


__all__ = ["DeviceFinder"]
