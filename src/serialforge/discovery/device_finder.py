"""QtCore facade for bounded serial discovery and baudrate probing."""

from __future__ import annotations

import threading
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)

from loguru import logger

# pylint: disable=no-name-in-module
from PySide6.QtCore import QCoreApplication, QObject, Signal

from serialforge.advanced import RuntimeConfig
from serialforge.discovery.async_scan_thread import AsyncScanThread
from serialforge.discovery.baud_cache import BaudCache
from serialforge.discovery.baud_prober import BaudProber
from serialforge.discovery.port_scanner import PortScanner
from serialforge.enums import ScanMode
from serialforge.errors import ProbeError, SerialForgeError
from serialforge.models import DeviceInfo, DeviceProfile
from serialforge.protocols import PortBackend
from serialforge.settings import LOG_ENABLED
from serialforge.transport import BackendSwitch, SerialTransport

# The finder coordinates cache, cancellation, and Qt worker ownership.
# pylint: disable=too-many-instance-attributes


class DeviceFinder(QObject):
    """Find matching devices while remembering successful baudrates.

    A QCoreApplication must exist before construction. Retain the finder until
    async completion; connect QObject slots in the application thread.

    Attributes:
        probe_progress: Emit (port, baudrate) for each probe attempt.
        probe_finished: Emit a list of DeviceInfo exactly once per scan.

    Example:
        For a blocking script use ``DeviceFinder(profile).find()``. In a Qt
        application connect probe_finished before ``finder.find_async()``.
    """

    probe_progress = Signal(str, int)
    probe_finished = Signal(object)

    def __init__(self, profile: DeviceProfile) -> None:
        """Create a finder without enumerating or opening any port.

        Args:
            profile: VID/PID filters, candidate baudrates and required probe.

        Raises:
            SerialForgeError: No QCoreApplication exists.
        """
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
        """Block until discovery completes, leaving enumeration to a worker.

        Args:
            mode: ALL returns every match; FIRST_MATCH stops other probes.
            port: Optional trusted port; bypasses VID/PID filtering.

        Returns:
            Matching devices with successful baudrates and probe responses.

        Raises:
            ProbeError: Another scan is already active on this finder.
        """
        self._begin()
        return self.run_find(mode, port)

    def find_async(
        self, mode: ScanMode = ScanMode.ALL, *, port: str | None = None
    ) -> None:
        """Return immediately and publish results through probe_finished.

        Args:
            mode: ALL or FIRST_MATCH completion policy.
            port: Optional trusted port, bypassing VID/PID filtering.

        Raises:
            ProbeError: Another scan is already active on this finder.
        """
        self._begin()
        thread = AsyncScanThread(self, mode, port)
        self._thread = thread
        application = QCoreApplication.instance()
        assert application is not None
        application.aboutToQuit.connect(self._shutdown)
        self._shutdown_hooked = True
        thread.start()

    def cancel(self) -> None:
        """Request cooperative cancellation; completion still emits a list."""
        self._cancel.set()

    def _shutdown(self) -> None:
        """Join an asynchronous scan before Qt destroys its thread."""
        self.cancel()
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.wait(5000)

    def forget(self, device: DeviceInfo) -> None:
        """Discard one remembered baudrate hint.

        Args:
            device: Identity whose cached baudrate should be forgotten.
        """
        self._cache.forget(device)

    def _begin(self) -> None:
        """Reject overlapping scans and reset cancellation state."""
        with self._lock:
            if self._active:
                raise ProbeError("a device scan is already active")
            self._active = True
            self._cancel.clear()

    def run_find(self, mode: ScanMode, port: str | None) -> list[DeviceInfo]:
        """Run the coordinator after a scan has been started internally.

        Args:
            mode: Completion policy passed by find or find_async.
            port: Optional port restriction passed by the public scan method.

        Returns:
            Completed probe results; use find or find_async to start a scan.
        """
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
