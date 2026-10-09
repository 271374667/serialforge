"""Thread-safe invocation results with application-thread callbacks."""

from __future__ import annotations

# One invocation owns its deadline, result and callback state.
# pylint: disable=too-many-instance-attributes,too-many-arguments
import inspect
import threading
import time
from collections.abc import Callable, Mapping
from typing import Generic, Self, TypeVar

from serialforge.advanced import CommandTicket
from serialforge.enums import CommandStatus, MessageCategory, SendRoute
from serialforge.errors import (
    CommandBusyError,
    CommandCancelledError,
    CommandDisconnectedError,
    CommandExecutionError,
    CommandNotReadyError,
    CommandParseError,
    CommandTimeoutError,
    DeviceCommandError,
    _without_tracebacks,
)
from serialforge.message import Message
from serialforge.models import CommandResult, CommandSpec, _freeze_context

T = TypeVar("T")
_ERRORS: dict[CommandStatus, type[CommandExecutionError]] = {
    CommandStatus.TIMEOUT: CommandTimeoutError,
    CommandStatus.CANCELLED: CommandCancelledError,
    CommandStatus.BUSY: CommandBusyError,
    CommandStatus.DISCONNECTED: CommandDisconnectedError,
    CommandStatus.PARSE_ERROR: CommandParseError,
    CommandStatus.DEVICE_ERROR: DeviceCommandError,
}


class CommandCall(CommandTicket, Generic[T]):
    """Retain one result and deliver successful callbacks on the Qt main thread.

    result() never blocks. Callbacks accept one Message, can be closures, and
    run only before the submission deadline. A late UI thread does not erase
    an already successful result. Retain/cancel calls when their UI is closed.
    """

    def __init__(
        self,
        request_id: str,
        spec: CommandSpec | None,
        route: SendRoute,
        *,
        context: Mapping[str, object],
        timeout_s: float,
        deadline: float,
        dispatch: Callable[[Callable[[], None]], None],
        cancel: Callable[[CommandTicket, CommandStatus], None],
    ) -> None:
        """Bind an invocation to a queued dispatcher and cancellation action."""
        super().__init__(request_id, spec, route)
        self.context: Mapping[str, object] = _freeze_context(context)
        self.timeout_s: float = timeout_s
        self.deadline: float = deadline
        self._dispatch = dispatch
        self._cancel_action = cancel
        self._lock: threading.RLock = threading.RLock()
        self._result: Message[T] | None = None
        self._exception: Exception | None = None
        self._callback_exception: Exception | None = None
        self._callbacks: list[Callable[[Message[T]], None]] = []
        self._callbacks_disabled: bool = False
        self._expired: bool = False
        self._done_event: threading.Event = threading.Event()
        self._timer: threading.Timer | None = None

    def _start_timer(self) -> None:
        """Start cleanup only after the dispatcher has accepted the ticket."""
        with self._lock:
            if self._exception is not None or self._callbacks_disabled:
                return
            timer = threading.Timer(
                max(0, self.deadline - time.monotonic()), self._expire
            )
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _expire(self) -> None:
        """Expire callbacks independently of Qt event processing."""
        with self._lock:
            self._expired = True
            self._callbacks.clear()
            if self._callback_exception is not None:
                _without_tracebacks(self._callback_exception)
            pending = self._result is None
        if pending:
            self._cancel_action(self, CommandStatus.TIMEOUT)

    # ty: ignore[missing-override-decorator] -- Python 3.11 runtime support.
    def _complete(self, result: object) -> None:
        """Atomically retain one result before any public signal is emitted."""
        assert isinstance(result, CommandResult)
        message: Message[T] = Message(
            category=MessageCategory.COMMAND_RESULT,
            timestamp=time.monotonic(),
            data=result.data,
            spec=result.spec,
            request_id=result.request_id or "",
            raw_frames=tuple(
                frame if isinstance(frame, bytes) else frame.encode()
                for frame in result.raw_frames
            ),
            context=self.context,
            status=result.status,
            route=result.route,
            params=result.params,
            sent=result.sent,
            elapsed_s=result.elapsed_s,
            error_message=result.error_message or "",
        )
        with self._lock:
            if self._result is not None:
                return
            self._result = message
            if not message.ok:
                self._exception = _ERRORS[message.status](message, result.cause)
                self._callbacks.clear()
                if self._timer is not None:
                    self._timer.cancel()
            self._done_event.set()
        if message.ok:
            self._dispatch(self._deliver)

    def _deliver(self) -> None:
        """Execute registered closures only through the main-thread bridge."""
        while True:
            with self._lock:
                if time.monotonic() >= self.deadline:
                    self._expired = True
                    self._callbacks.clear()
                if (
                    self._expired
                    or self._callbacks_disabled
                    or not self._callbacks
                ):
                    return
                message = self._result
                if message is None or not message.ok:
                    return
                callback = self._callbacks.pop(0)
            try:
                callback(message)
            except Exception as exc:
                with self._lock:
                    self._callback_exception = exc.with_traceback(None)
                # Qt's dispatcher reports the exception through the host's
                # existing exception hook; each remaining callback is queued.
                self._dispatch(self._deliver)
                raise

    def add_done_callback(
        self, callback: Callable[[Message[T]], None], /
    ) -> Self:
        """Register a closure; successful replay is always queued."""
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
                    "callback must accept one positional Message"
                ) from exc
        with self._lock:
            if time.monotonic() >= self.deadline:
                self._expired = True
            if (
                self._expired
                or self._callbacks_disabled
                or self._exception is not None
            ):
                return self
            self._callbacks.append(callback)
            ready = self._result is not None
        if ready:
            self._dispatch(self._deliver)
        return self

    def result(self) -> Message[T]:
        """Return the stored success, or raise execution/pending errors."""
        with self._lock:
            if self._exception is not None:
                raise self._exception
            if self._result is None:
                raise CommandNotReadyError("command has not completed")
            return self._result

    # ty: ignore[missing-override-decorator] -- Python 3.11 runtime support.
    def cancel(self) -> None:
        """Cancel pending work or suppress callbacks after successful
        completion.
        """
        with self._lock:
            self._cancelled = True
            self._callbacks_disabled = True
            self._callbacks.clear()
            if self._timer is not None:
                self._timer.cancel()
        self._cancel_action(self, CommandStatus.CANCELLED)

    @property
    def done(self) -> bool:
        """Return whether a terminal result has been retained."""
        return self._done_event.is_set()

    @property
    def callback_expired(self) -> bool:
        """Return whether the successful callback delivery deadline expired."""
        return self._expired or time.monotonic() >= self.deadline

    @property
    def exception(self) -> Exception | None:
        """Return the saved execution failure, if completed unsuccessfully."""
        return self._exception

    @property
    def callback_exception(self) -> Exception | None:
        """Return the most recent error raised by a user callback."""
        return self._callback_exception


__all__ = ["CommandCall"]
