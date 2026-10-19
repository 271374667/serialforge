"""Thread-safe asynchronous scan results with main-thread callbacks."""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from typing import Self

from serialforge.errors import CommandNotReadyError, _without_tracebacks
from serialforge.models import DeviceInfo

# One scan owns its result, cancellation and callback state.
# pylint: disable=too-many-instance-attributes


class ScanCall:
    """Retain one asynchronous scan and deliver results on the main thread.

    A callback accepts the discovered ``list[DeviceInfo]`` directly and runs
    through the facade's Qt bridge.  A failed scan never runs a success
    callback; read ``result()`` to re-raise its error.  There is no extra
    delivery deadline: the scan's own probe budget bounds it.
    """

    def __init__(
        self,
        *,
        dispatch: Callable[[Callable[[], None]], None],
        cancel: Callable[[], None],
    ) -> None:
        """Bind one scan to the facade's bridge and cancellation action."""
        self._dispatch = dispatch
        self._cancel_action = cancel
        self._lock: threading.RLock = threading.RLock()
        self._devices: list[DeviceInfo] | None = None
        self._exception: Exception | None = None
        self._callback_exception: Exception | None = None
        self._callbacks: list[Callable[[list[DeviceInfo]], None]] = []
        self._cancelled: bool = False
        self._done_event: threading.Event = threading.Event()
        # Workers check this before and during discovery so a cancel that
        # arrives before the finder exists is not lost.
        self._cancel_event: threading.Event = threading.Event()

    def _complete(self, devices: list[DeviceInfo]) -> None:
        """Retain one scan result and queue successful callbacks."""
        with self._lock:
            if self._done_event.is_set():
                return
            self._devices = list(devices)
            deliver = not self._cancelled and bool(self._callbacks)
            self._done_event.set()
        if deliver:
            self._dispatch(self._deliver)

    def _fail(self, error: Exception) -> None:
        """Retain a scan failure; success callbacks are never run."""
        with self._lock:
            if self._done_event.is_set():
                return
            self._exception = _without_tracebacks(error)
            self._callbacks.clear()
            self._done_event.set()

    def _deliver(self) -> None:
        """Run registered closures only through the main-thread bridge."""
        while True:
            with self._lock:
                if self._cancelled or not self._callbacks:
                    return
                devices = self._devices
                if devices is None:
                    return
                callback = self._callbacks.pop(0)
            try:
                callback(list(devices))
            except Exception as exc:
                with self._lock:
                    self._callback_exception = exc.with_traceback(None)
                self._dispatch(self._deliver)
                raise

    def add_done_callback(
        self, callback: Callable[[list[DeviceInfo]], None], /
    ) -> Self:
        """Register a closure; a completed scan replays on the main thread."""
        if not callable(callback):
            raise TypeError("callback must be callable")
        try:
            signature = inspect.signature(callback)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            try:
                signature.bind(object())
            except TypeError as exc:
                raise TypeError(
                    "callback must accept one positional device list"
                ) from exc
        with self._lock:
            if self._cancelled or self._exception is not None:
                return self
            self._callbacks.append(callback)
            ready = self._done_event.is_set() and self._devices is not None
        if ready:
            self._dispatch(self._deliver)
        return self

    def result(self) -> list[DeviceInfo]:
        """Return the discovered devices or raise the retained failure.

        A cancelled scan keeps the matches completed before cancellation,
        matching the blocking scan contract.
        """
        with self._lock:
            if self._exception is not None:
                raise self._exception
            if self._devices is None:
                raise CommandNotReadyError("scan has not completed")
            return list(self._devices)

    def cancel(self) -> None:
        """Request cancellation and suppress success callbacks."""
        with self._lock:
            self._cancelled = True
            self._callbacks.clear()
        self._cancel_event.set()
        self._cancel_action()

    @property
    def done(self) -> bool:
        """Return whether the scan retained a terminal outcome."""
        return self._done_event.is_set()

    @property
    def cancelled(self) -> bool:
        """Return whether cancellation was requested."""
        return self._cancelled

    @property
    def exception(self) -> Exception | None:
        """Return the saved scan failure, if any."""
        return self._exception

    @property
    def callback_exception(self) -> Exception | None:
        """Return the most recent error raised by a user callback."""
        return self._callback_exception


__all__ = ["ScanCall"]
