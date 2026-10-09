"""QtCore facade for bounded serial discovery and baudrate probing."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import (
    TimeoutError as FutureTimeoutError,
)

from loguru import logger

from serialforge.advanced import RuntimeConfig
from serialforge.discovery.baud_cache import BaudCache
from serialforge.discovery.baud_prober import BaudProber
from serialforge.discovery.port_scanner import PortScanner
from serialforge.enums import ScanMode
from serialforge.errors import ProbeError, SerialForgeError
from serialforge.models import DeviceInfo, DeviceProfile
from serialforge.protocols import PortBackend

# pylint: disable=no-name-in-module
from serialforge.qt_core import QCoreApplication, QObject, Signal
from serialforge.settings import LOG_ENABLED
from serialforge.transport import BackendSwitch, SerialTransport

# The finder coordinates cache, cancellation, and Qt worker ownership.
# pylint: disable=too-many-instance-attributes


class DeviceFinder(QObject):
    """Find matching devices while remembering successful baudrates.

    A QCoreApplication must exist before construction. ``find`` blocks its
    caller while enumeration and probing run in worker threads. Applications
    can call it in their own worker thread for asynchronous behavior.

    Attributes:
        probe_progress: Emit (port, baudrate) for each probe attempt.
        probe_finished: Emit a list of DeviceInfo exactly once per scan.

    Example:
        Use ``DeviceFinder(profile).find()`` for all matching devices, or
        ``find(ScanMode.FIRST_MATCH)`` to cancel other probes after a match.
    """

    probe_progress = Signal(str, int)
    probe_finished = Signal(object)

    def __init__(self, profile: DeviceProfile) -> None:
        """Create a finder without enumerating or opening any port.

        Args:
            profile: Optional VID/PID filters, required baudrates and probe.

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
        self._strict_errors: bool = False
        self._preserve_cancel: bool = False
        self._raw_received: Callable[[bytes], None] | None = None
        self._raw_sent: Callable[[bytes], None] | None = None

    def find(
        self, mode: ScanMode = ScanMode.ALL, *, port: str | None = None
    ) -> list[DeviceInfo]:
        """Wait for discovery and all probe handles to be closed.

        Args:
            mode: ALL returns every match; FIRST_MATCH stops other probes.
            port: Optional trusted port; bypasses VID/PID filtering.

        Returns:
            Matching devices with successful baudrates and probe responses.

        Note:
            Different ports are probed concurrently; rates on one port are
            tried serially. FIRST_MATCH cooperatively cancels other probes and
            joins their workers before returning. No Qt event loop is needed
            to obtain the return value.

        Raises:
            ProbeError: Another scan is already active on this finder.
        """
        with self._lock:
            if self._active:
                raise ProbeError("a device scan is already active")
            self._active = True
            if not self._preserve_cancel:
                self._cancel.clear()
        return self.run_find(mode, port)

    def cancel(self) -> None:
        """Request cooperative cancellation; completion still emits a list."""
        self._cancel.set()

    def forget(self, device: DeviceInfo) -> None:
        """Discard one remembered baudrate hint.

        Args:
            device: Identity whose cached baudrate should be forgotten.
        """
        self._cache.forget(device)

    def run_find(self, mode: ScanMode, port: str | None) -> list[DeviceInfo]:
        """Run the coordinator after a scan has been started internally.

        Args:
            mode: Completion policy passed by find.
            port: Optional port restriction passed by the public scan method.

        Returns:
            Completed probe results; use find to start a scan.
        """
        result: list[DeviceInfo] = []
        try:
            with ThreadPoolExecutor(max_workers=1) as coordinator:
                result = coordinator.submit(self._scan, mode, port).result()
        except (OSError, RuntimeError) as exc:
            if LOG_ENABLED:
                logger.debug("device scan failed: {}", exc)
            raise ProbeError(f"device scan failed: {exc}") from exc
        finally:
            with self._lock:
                self._active = False
            self.probe_finished.emit(result)
        return result

    def _scan(self, mode: ScanMode, port: str | None) -> list[DeviceInfo]:
        """Probe separate ports concurrently and stop on the first match."""
        runtime = self._profile.runtime
        assert isinstance(runtime, RuntimeConfig)
        deadline = time.monotonic() + runtime.probe_total_timeout_s
        candidates = PortScanner(self._profile, self._backend).scan(port)
        prober = BaudProber(
            self._profile,
            self._backend,
            self._cache,
            raw_received=self._raw_received,
            raw_sent=self._raw_sent,
            strict_errors=self._strict_errors,
        )
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
                        raise ProbeError(
                            f"device probe worker failed: {exc}"
                        ) from exc
                    if device is not None:
                        results.append(device)
                        if mode is ScanMode.FIRST_MATCH:
                            self._cancel.set()
                            for pending in futures:
                                pending.cancel()
                            break
            except FutureTimeoutError:
                self._cancel.set()
                for pending in futures:
                    pending.cancel()
        return results[:1] if mode is ScanMode.FIRST_MATCH else results


__all__ = ["DeviceFinder"]
