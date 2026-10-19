"""Public facade for declaration, discovery and serial command execution."""

from __future__ import annotations

# The facade composes private lifecycle components; explicit field overloads
# are the approved public API, and the retained script application is shared.
# pylint: disable=protected-access,too-many-instance-attributes,too-many-arguments,too-many-boolean-expressions,too-many-locals,global-statement
import math
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Literal, TypeVar, overload

from serialforge.advanced import (
    CommandPriority,
    CommandTicket,
    FramingConfig,
    HandlerStats,
    ProbeSpec,
    RuntimeConfig,
    SerialConfig,
)
from serialforge.connection.command_call import CommandCall
from serialforge.connection.command_registry import CommandRegistry
from serialforge.connection.connect_call import ConnectCall
from serialforge.connection.handler_core import HandlerCore
from serialforge.connection.scan_call import ScanCall
from serialforge.discovery.device_finder import DeviceFinder
from serialforge.enums import (
    ConnectionState,
    Correlation,
    FramingMode,
    MessageCategory,
    ResponseMode,
    ScanMode,
    SendRoute,
    SpecRole,
    TimeoutPolicy,
)
from serialforge.errors import (
    CommandError,
    ConfigError,
    FramingError,
    SerialForgeError,
    _without_tracebacks,
)
from serialforge.message import Message
from serialforge.models import (
    CommandResult,
    CommandSpec,
    DeviceEvent,
    DeviceInfo,
    DeviceProfile,
    EventSpec,
    LogConfig,
    _freeze_context,
)
from serialforge.qt_core import (
    QCoreApplication,
    QObject,
    Qt,
    QThread,
    Signal,
    Slot,
)
from serialforge.spec import Spec
from serialforge.transport import FrameSplitter

T = TypeVar("T")
_CORE_APPLICATION: QCoreApplication | None = None
_EMPTY: Mapping[str, object] = MappingProxyType({})


class SerialForge(QObject):
    """Own one device and expose blocking and asynchronous command APIs.

    Create on the Qt application's main thread. application="core" creates a
    retained QCoreApplication for scripts; callers still own its event loop.
    Configure with a DeviceProfile or plain fields, then add declarations and
    connect. scan_sync/connect_sync/send_sync block their caller and may run
    in an application worker; scan_async/connect_async return call handles
    with main-thread callbacks. received/raw_sent and done callbacks are
    queued on the main thread. Only QtCore is imported.

    Attributes:
        received: Message with nonempty raw_data, using complete LINE reads.
        raw_sent: Exact bytes accepted by the serial backend, including probes.
        connection_state_changed: New ConnectionState on the main thread.
    """

    received = Signal(object)
    raw_sent = Signal(bytes)
    connection_state_changed = Signal(object)
    _dispatch_requested = Signal(object)

    def __init__(
        self,
        profile: DeviceProfile | None = None,
        *,
        log: LogConfig | None = None,
        allow_raw_text: bool = True,
        application: Literal["existing", "core"] = "existing",
    ) -> None:
        """Create a disconnected facade without opening serial resources."""
        global _CORE_APPLICATION
        if application not in ("existing", "core"):
            raise ConfigError("application must be existing or core")
        app = QCoreApplication.instance()
        if app is None and application == "core":
            if threading.current_thread() is not threading.main_thread():
                raise ConfigError(
                    "create QCoreApplication on the Python main thread"
                )
            _CORE_APPLICATION = QCoreApplication([])
            app = _CORE_APPLICATION
        if app is None:
            raise ConfigError(
                "create QApplication or QCoreApplication before SerialForge"
            )
        if QThread.currentThread() != app.thread():
            raise ConfigError(
                "create SerialForge on the Qt application main thread"
            )
        super().__init__()
        self._core: HandlerCore | None = None
        self._registry: CommandRegistry = CommandRegistry()
        self._profile: DeviceProfile | None = None
        self._log: LogConfig = log or LogConfig()
        self._allow_raw_text: bool = allow_raw_text
        self._lock: threading.RLock = threading.RLock()
        self._receive_framing: FramingConfig = FramingConfig()
        self._receive_splitters: dict[int, FrameSplitter] = {}
        self._calls: dict[str, CommandCall[Any]] = {}
        self._finder: DeviceFinder | None = None
        self._scanning: bool = False
        self._connecting: bool = False
        self._submitting: int = 0
        self._scan_done: threading.Event = threading.Event()
        self._scan_done.set()
        self._async_scan: ScanCall | None = None
        self._async_scan_active: bool = False
        self._async_thread: threading.Thread | None = None
        self._async_connect: ConnectCall | None = None
        self._async_connect_active: bool = False
        self._async_connect_thread: threading.Thread | None = None
        self._callback_calls: weakref.WeakSet[CommandCall[Any]] = (
            weakref.WeakSet()
        )
        self._errors: deque[Exception] = deque()
        self._dropped_errors: int = 0
        self._dispatch_requested.connect(
            self._run_dispatch, Qt.ConnectionType.QueuedConnection
        )
        app.aboutToQuit.connect(self.disconnect)
        if profile is not None:
            self.configure(profile)

    @Slot(object)
    def _run_dispatch(self, action: Callable[[], None]) -> None:
        """Invoke queued work using QObject affinity rather than closure
        affinity.
        """
        action()

    def _dispatch(self, action: Callable[[], None]) -> None:
        """Always queue public delivery, including main-thread submissions."""
        self._dispatch_requested.emit(action)

    def _publish(self, message: Message[Any]) -> None:
        """Publish only messages carrying nonempty original receive bytes."""
        if not message.raw_data:
            return
        self._dispatch(lambda: self.received.emit(message))

    def _on_result(self, result: CommandResult) -> None:
        """Release completed calls and apply the common receive filter."""
        with self._lock:
            call = self._calls.pop(result.request_id or "", None)
        if call is not None:
            message = call._result
            assert message is not None
        else:
            message = Message(
                MessageCategory.COMMAND_RESULT,
                time.monotonic(),
                data=result.data,
                spec=result.spec,
                request_id=result.request_id or "",
                context=result.context,
                status=result.status,
                route=result.route,
                params=result.params,
                sent=result.sent,
                raw_frames=tuple(
                    frame if isinstance(frame, bytes) else frame.encode()
                    for frame in result.raw_frames
                ),
                elapsed_s=result.elapsed_s,
                error_message=result.error_message or "",
            )
        self._publish(message)

    def _on_event(self, event: DeviceEvent) -> None:
        """Normalize push, stream and unknown frames into one message model."""
        category = (
            MessageCategory.STREAM_FRAME
            if event.request_id
            else MessageCategory.DEVICE_EVENT
            if event.spec is not None
            else MessageCategory.UNKNOWN_FRAME
        )
        self._publish(
            Message(
                category,
                event.timestamp,
                data=event.data,
                spec=event.spec,
                request_id=event.request_id or "",
                raw_frames=(
                    event.raw_frame
                    if isinstance(event.raw_frame, bytes)
                    else event.raw_frame.encode(),
                ),
                context=event.context,
            )
        )

    def _on_response(
        self,
        observation: tuple[
            CommandTicket, bytes, Any, float, Mapping[str, object]
        ],
    ) -> None:
        """Apply the common receive filter to matched response observations."""
        ticket, frame, data, timestamp, params = observation
        assert ticket.spec is None or isinstance(ticket.spec, CommandSpec)
        self._publish(
            Message(
                MessageCategory.RESPONSE_FRAME,
                timestamp,
                data=data,
                spec=ticket.spec,
                request_id=ticket.request_id,
                raw_frames=(frame,),
                params=params,
                context=ticket.context
                if isinstance(ticket, CommandCall)
                else {},
                route=ticket.route,
            )
        )

    def _on_raw_receive(self, data: bytes) -> None:
        """Join LINE reads before delivery, retaining the receive terminator.

        Other framing modes keep nonempty read chunks. Separate splitters
        prevent parallel probe threads from mixing their partial lines.
        """
        if not data:
            return
        with self._lock:
            framing = self._receive_framing
            if framing.mode is FramingMode.LINE:
                source = threading.get_ident()
                splitter = self._receive_splitters.get(source)
                if splitter is None:
                    splitter = FrameSplitter(framing)
                    self._receive_splitters[source] = splitter
                try:
                    frames = splitter.feed(data)
                except FramingError as error:
                    self._receive_splitters.pop(source, None)
                    self._on_error(error)
                    return
                chunks = [
                    frame + framing.terminator for frame in frames if frame
                ]
            else:
                chunks = [data]
            for chunk in chunks:
                self._publish(
                    Message(
                        MessageCategory.RAW_RECEIVE,
                        time.monotonic(),
                        raw_data=chunk,
                    )
                )

    def _on_connection_state_changed(self, state: ConnectionState) -> None:
        """Clear partial lines when changing serial sessions and queue state."""
        with self._lock:
            if state is not ConnectionState.CONNECTED:
                self._receive_splitters.clear()
        self._dispatch(lambda: self.connection_state_changed.emit(state))

    def _on_raw_sent(self, data: bytes) -> None:
        """Queue bytes actually accepted by the backend."""
        with self._lock:
            if self._scanning or self.state is ConnectionState.PROBING:
                # One probe thread may try multiple ports or baudrates.
                self._receive_splitters.pop(threading.get_ident(), None)
        self._dispatch(lambda: self.raw_sent.emit(data))

    def _on_error(self, error: Exception) -> None:
        """Retain unowned background errors for explicit caller-side raising."""
        with self._lock:
            if self._connecting:
                return  # connect raises its own startup failure.
            if len(self._errors) == 128:
                self._errors.popleft()
                self._dropped_errors += 1
            self._errors.append(_without_tracebacks(error))

    @property
    def state(self) -> ConnectionState:
        """Return current connection state without processing Qt events."""
        return self._core.state if self._core else ConnectionState.DISCONNECTED

    @overload
    def configure(self, profile: DeviceProfile, /) -> None: ...

    @overload
    def configure(
        self,
        /,
        *,
        baudrates: Sequence[int],
        probe: Spec[Any] | ProbeSpec,
        vid_pid: Sequence[tuple[int, int]] = (),
        name: str = "",
        terminator: str = "\r\n",
        encoding: str = "utf-8",
        echo: bool = False,
        heartbeat_s: float = -1.0,
        serial: SerialConfig | None = None,
        framing: FramingConfig | None = None,
        runtime: RuntimeConfig | None = None,
    ) -> None: ...

    @overload
    def configure(
        self,
        /,
        *,
        baudrates: Sequence[int],
        probe: str,
        probe_pattern: str,
        probe_timeout_s: float = 1.0,
        vid_pid: Sequence[tuple[int, int]] = (),
        name: str = "",
        terminator: str = "\r\n",
        encoding: str = "utf-8",
        echo: bool = False,
        heartbeat_s: float = -1.0,
        serial: SerialConfig | None = None,
        framing: FramingConfig | None = None,
        runtime: RuntimeConfig | None = None,
    ) -> None: ...

    def configure(
        self, profile: DeviceProfile | None = None, /, **fields: Any
    ) -> None:
        """Replace idle configuration while preserving declaration identity.

        Pass either an existing profile or required baudrates/probe fields.
        A text probe also requires probe_pattern. Heartbeat -1 disables it.
        Configuration changes are rejected during scan, connection or sends.
        """
        if profile is not None and fields:
            raise ConfigError(
                "profile cannot be combined with configuration fields"
            )
        if profile is None:
            options = dict(fields)
            probe = options.get("probe")
            if isinstance(probe, str):
                if "probe_pattern" not in options:
                    raise ConfigError("text probe requires probe_pattern")
                options["probe"] = Spec(
                    probe,
                    options.pop("probe_pattern"),
                    timeout_s=options.pop("probe_timeout_s", 1.0),
                )
            elif "probe_pattern" in options or "probe_timeout_s" in options:
                raise ConfigError("probe object cannot have text probe options")
            heartbeat = options.pop("heartbeat_s", -1.0)
            if (
                type(heartbeat) not in (int, float)
                or not math.isfinite(heartbeat)
                or (heartbeat <= 0 and heartbeat != -1)
            ):
                raise ConfigError(
                    "heartbeat_s must be -1 or finite positive seconds"
                )
            options["heartbeat_s"] = None if heartbeat == -1 else heartbeat
            options["name"] = options.get("name") or None
            profile = DeviceProfile(**options)
        if not isinstance(profile, DeviceProfile):
            raise ConfigError("profile must be DeviceProfile")
        with self._lock:
            if (
                self._scanning
                or self._connecting
                or (
                    self._async_scan_active
                    and threading.current_thread() is not self._async_thread
                )
                or (
                    self._async_connect_active
                    and threading.current_thread()
                    is not self._async_connect_thread
                )
                or self._calls
                or self._submitting
                or (
                    self._core is not None
                    and self._core._worker is not None
                    and self._core._worker.isRunning()
                )
                or self.state
                not in (ConnectionState.DISCONNECTED, ConnectionState.FAILED)
            ):
                raise ConfigError(
                    "configuration requires a disconnected idle facade"
                )
            candidate = CommandRegistry(profile.terminator)
            candidate.register(*self._registry.commands, *self._registry.events)
            receive_framing = profile.framing or FramingConfig(
                terminator=profile.terminator.encode(profile.encoding)
            )
            core = HandlerCore(
                profile, log=self._log, allow_raw_text=self._allow_raw_text
            )
            core._registry = candidate
            core._dispatcher._registry = candidate
            for obj in (core._dispatcher, core._traffic, core):
                obj.moveToThread(self.thread())
            direct = Qt.ConnectionType.DirectConnection
            core.command_finished.connect(self._on_result, direct)
            core.event_received.connect(self._on_event, direct)
            core.raw_received.connect(self._on_raw_receive, direct)
            core.raw_sent.connect(self._on_raw_sent, direct)
            core.error_occurred.connect(self._on_error, direct)
            core._dispatcher.response_frame.connect(self._on_response, direct)
            core.connection_state_changed.connect(
                self._on_connection_state_changed,
                direct,
            )
            if self._core is not None:
                core.set_init_sequence(list(self._core._init_sequence))
                self._core.disconnect()
                app = QCoreApplication.instance()
                assert app is not None
                app.aboutToQuit.disconnect(self._core.disconnect)
                self._core.deleteLater()
            self._profile = profile
            self._registry = candidate
            self._core = core
            self._receive_framing = receive_framing
            self._receive_splitters.clear()

    @overload
    def add(self, spec: Spec[T], /) -> Spec[T]: ...

    @overload
    def add(
        self, spec: CommandSpec | EventSpec, /
    ) -> CommandSpec | EventSpec: ...

    @overload
    def add(
        self, request: str, pattern: str, result_type: type[T], **options: Any
    ) -> Spec[T]: ...

    @overload
    def add(self, request: str, pattern: str, **options: Any) -> Spec[str]: ...

    @overload
    def add(
        self,
        request: str,
        pattern: str,
        *,
        name: str = "",
        mode: ResponseMode = ResponseMode.SINGLE,
        correlation: Correlation = Correlation.EXCLUSIVE,
        count: int = -1,
        until: str = "",
        error_pattern: str = "",
        timeout_s: float | TimeoutPolicy = 1.0,
        idle_timeout_s: float = 0.3,
        total_timeout_s: float = -1.0,
        idempotent: bool = False,
        parser: Callable[[str], str] | Literal[""] = "",
    ) -> Spec[str]: ...

    @overload
    def add(
        self,
        request: str,
        pattern: str,
        result_type: type[T],
        *,
        name: str = "",
        mode: ResponseMode = ResponseMode.SINGLE,
        correlation: Correlation = Correlation.EXCLUSIVE,
        count: int = -1,
        until: str = "",
        error_pattern: str = "",
        timeout_s: float | TimeoutPolicy = 1.0,
        idle_timeout_s: float = 0.3,
        total_timeout_s: float = -1.0,
        idempotent: bool = False,
        parser: Callable[[str], T] | Literal[""] = "",
    ) -> Spec[T]: ...

    @overload
    def add(
        self,
        request: str,
        *,
        mode: Literal[ResponseMode.NO_REPLY],
        name: str = "",
        idempotent: bool = False,
    ) -> Spec[Any]: ...

    @overload
    def add(
        self,
        *,
        pattern: str,
        result_type: type[T],
        name: str = "",
        parser: Callable[[str], T] | Literal[""] = "",
    ) -> Spec[T]: ...

    @overload
    def add(
        self,
        *,
        pattern: str,
        name: str = "",
        parser: Callable[[str], str] | Literal[""] = "",
    ) -> Spec[str]: ...

    def add(self, *args: Any, **options: Any) -> CommandSpec | EventSpec:
        """Register an existing declaration or construct one from fields."""
        if args and isinstance(args[0], (CommandSpec, EventSpec)):
            if len(args) != 1 or options:
                raise CommandError(
                    "spec object cannot be combined with declaration fields"
                )
            spec = args[0]
        else:
            spec = Spec(*args, **options)
        with self._lock:
            self._registry.register(spec)
        return spec

    def remove(self, spec: CommandSpec | EventSpec, /) -> None:
        """Remove the exact declaration from future routing."""
        with self._lock:
            self._registry.unregister(spec)

    @overload
    def scan_async(
        self, /, *, mode: ScanMode = ScanMode.ALL, port: str = ""
    ) -> ScanCall: ...

    @overload
    def scan_async(
        self,
        profile: DeviceProfile,
        /,
        *,
        mode: ScanMode = ScanMode.ALL,
        port: str = "",
    ) -> ScanCall: ...

    @overload
    def scan_async(
        self,
        /,
        *,
        mode: ScanMode = ScanMode.ALL,
        port: str = "",
        baudrates: Sequence[int],
        probe: Spec[Any] | ProbeSpec,
        vid_pid: Sequence[tuple[int, int]] = (),
        name: str = "",
        terminator: str = "\r\n",
        encoding: str = "utf-8",
        echo: bool = False,
        heartbeat_s: float = -1.0,
        serial: SerialConfig | None = None,
        framing: FramingConfig | None = None,
        runtime: RuntimeConfig | None = None,
    ) -> ScanCall: ...

    @overload
    def scan_async(
        self,
        /,
        *,
        mode: ScanMode = ScanMode.ALL,
        port: str = "",
        baudrates: Sequence[int],
        probe: str,
        probe_pattern: str,
        probe_timeout_s: float = 1.0,
        vid_pid: Sequence[tuple[int, int]] = (),
        name: str = "",
        terminator: str = "\r\n",
        encoding: str = "utf-8",
        echo: bool = False,
        heartbeat_s: float = -1.0,
        serial: SerialConfig | None = None,
        framing: FramingConfig | None = None,
        runtime: RuntimeConfig | None = None,
    ) -> ScanCall: ...

    def scan_async(
        self,
        profile: DeviceProfile | None = None,
        /,
        *,
        mode: ScanMode = ScanMode.ALL,
        port: str = "",
        **fields: Any,
    ) -> ScanCall:
        """Start a scan in a worker thread and return its call handle.

        The worker first disconnects the current session, exactly like
        scan_sync, so a connected facade can be rescanned directly.
        add_done_callback closures receive the discovered list[DeviceInfo] on
        the Qt main thread, so the caller never blocks. Failures never run a
        success closure and are raised by result(). There is no extra delivery
        deadline; the scan's own probe budget bounds it. cancel() requests
        cancellation and suppresses callbacks; result() then returns the
        matches completed so far.
        """
        with self._lock:
            if (
                self._scanning
                or self._connecting
                or self._async_scan_active
                or self._async_connect_active
            ):
                raise ConfigError("scan or connection is already active")
            if profile is None and not fields and self._profile is None:
                raise ConfigError("configure before scanning")
            call = ScanCall(dispatch=self._dispatch, cancel=self.cancel_scan)
            self._async_scan = call
            self._async_scan_active = True
            options = dict(fields)

            def run_scan() -> None:
                error: Exception | None = None
                devices: list[DeviceInfo] = []
                try:
                    devices = self._scan_request(
                        profile,
                        mode,
                        port,
                        options,
                        cancel_event=call._cancel_event,
                    )
                except BaseException as exc:  # pylint: disable=broad-exception-caught
                    error = (
                        exc
                        if isinstance(exc, Exception)
                        else RuntimeError(str(exc))
                    )
                # Release the facade before callbacks are queued so a closure
                # may start the next operation (connect/scan) directly.
                with self._lock:
                    self._async_scan_active = False
                try:
                    if error is not None:
                        call._fail(error)
                    else:
                        call._complete(devices)
                finally:
                    with self._lock:
                        if self._async_scan is call:
                            self._async_scan = None
                            self._async_thread = None

            thread = threading.Thread(
                target=run_scan,
                name="serialforge-scan",
                daemon=False,
            )
            self._async_thread = thread
        try:
            thread.start()
        except BaseException:
            with self._lock:
                self._async_scan = None
                self._async_scan_active = False
                self._async_thread = None
            raise
        return call

    @overload
    def scan_sync(
        self, /, *, mode: ScanMode = ScanMode.ALL, port: str = ""
    ) -> list[DeviceInfo]: ...

    @overload
    def scan_sync(
        self,
        profile: DeviceProfile,
        /,
        *,
        mode: ScanMode = ScanMode.ALL,
        port: str = "",
    ) -> list[DeviceInfo]: ...

    @overload
    def scan_sync(
        self,
        /,
        *,
        mode: ScanMode = ScanMode.ALL,
        port: str = "",
        baudrates: Sequence[int],
        probe: Spec[Any] | ProbeSpec,
        vid_pid: Sequence[tuple[int, int]] = (),
        name: str = "",
        terminator: str = "\r\n",
        encoding: str = "utf-8",
        echo: bool = False,
        heartbeat_s: float = -1.0,
        serial: SerialConfig | None = None,
        framing: FramingConfig | None = None,
        runtime: RuntimeConfig | None = None,
    ) -> list[DeviceInfo]: ...

    @overload
    def scan_sync(
        self,
        /,
        *,
        mode: ScanMode = ScanMode.ALL,
        port: str = "",
        baudrates: Sequence[int],
        probe: str,
        probe_pattern: str,
        probe_timeout_s: float = 1.0,
        vid_pid: Sequence[tuple[int, int]] = (),
        name: str = "",
        terminator: str = "\r\n",
        encoding: str = "utf-8",
        echo: bool = False,
        heartbeat_s: float = -1.0,
        serial: SerialConfig | None = None,
        framing: FramingConfig | None = None,
        runtime: RuntimeConfig | None = None,
    ) -> list[DeviceInfo]: ...

    def scan_sync(
        self,
        profile: DeviceProfile | None = None,
        /,
        *,
        mode: ScanMode = ScanMode.ALL,
        port: str = "",
        **fields: Any,
    ) -> list[DeviceInfo]:
        """Disconnect, apply optional configuration, then scan for devices.

        Returns discovered devices and never connects them automatically.
        Ports and rates that cannot be opened, written or responded are
        skipped, so no match returns an empty list instead of raising; only
        invalid configuration or concurrent misuse raises. Use scan_async
        for a main-thread callback instead of blocking. Raw probe traffic
        uses the same two observation signals; TX bytes do not identify
        concurrent ports.
        """
        return self._scan_request(profile, mode, port, dict(fields))

    def _scan_request(
        self,
        profile: DeviceProfile | None,
        mode: ScanMode,
        port: str,
        fields: dict[str, Any],
        *,
        cancel_event: threading.Event | None = None,
    ) -> list[DeviceInfo]:
        """Run one blocking scan after reserving the facade lifecycle."""
        async_worker = threading.current_thread() is self._async_thread
        with self._lock:
            if (
                self._scanning
                or self._connecting
                or (self._async_scan_active and not async_worker)
                or self._async_connect_active
            ):
                raise ConfigError("scan or connection is already active")
            # Reserve lifecycle changes while stopping outside the facade lock.
            self._connecting = True
        if cancel_event is not None and cancel_event.is_set():
            with self._lock:
                self._connecting = False
            return []
        try:
            self.disconnect()
        except BaseException:
            with self._lock:
                self._connecting = False
            raise
        with self._lock:
            self._connecting = False
            if profile is not None or fields:
                if profile is not None:
                    self.configure(profile)
                else:
                    self.configure(**fields)
            if self._profile is None:
                raise ConfigError("configure before scanning")
            if (
                self._scanning
                or self._connecting
                or (self._async_scan_active and not async_worker)
                or self._async_connect_active
                or self.state
                not in (ConnectionState.DISCONNECTED, ConnectionState.FAILED)
            ):
                raise ConfigError("scan requires a disconnected idle facade")
            finder = DeviceFinder(self._profile)
            finder._preserve_cancel = True
            finder._raw_received = self._on_raw_receive
            finder._raw_sent = self._on_raw_sent
            self._finder = finder
            self._receive_splitters.clear()
            self._scanning = True
            self._scan_done.clear()
        if cancel_event is not None and cancel_event.is_set():
            finder.cancel()
        try:
            return finder.find(mode, port=port or None)
        finally:
            with self._lock:
                self._receive_splitters.clear()
                self._scanning = False
                self._finder = None
                self._scan_done.set()

    def cancel_scan(self) -> None:
        """Request cancellation from any thread; scan joins before returning."""
        with self._lock:
            if self._finder is not None:
                self._finder.cancel()

    def connect_sync(self, target: str | DeviceInfo = "") -> None:
        """Close the previous session, then block until the target is ready.

        Even the same target starts a fresh connection and initialization.
        Pending calls and callbacks are cancelled before opening the target.
        Use connect_async for a main-thread callback instead of blocking.
        """
        self._connect_request(target or None)

    def connect_async(self, target: str | DeviceInfo = "") -> ConnectCall:
        """Start a connection in a worker thread and return its call handle.

        The worker first disconnects the current session, exactly like
        connect_sync. add_done_callback closures receive the connected
        DeviceInfo on the Qt main thread, so the caller never blocks. Failures
        never run a success closure and are raised by result(). cancel()
        stops a pending attempt and suppresses callbacks.
        """
        with self._lock:
            self._require_core()
            if (
                self._scanning
                or self._connecting
                or self._async_scan_active
                or self._async_connect_active
            ):
                raise ConfigError("scan or connection is already active")
            call = ConnectCall(
                dispatch=self._dispatch,
                cancel=self._cancel_pending_connect,
            )
            self._async_connect = call
            self._async_connect_active = True
            resolved = target or None

            def run_connect() -> None:
                error: Exception | None = None
                device: DeviceInfo | None = None
                try:
                    device = self._connect_request(
                        resolved,
                        cancel_event=call._cancel_event,
                    )
                except BaseException as exc:  # pylint: disable=broad-exception-caught
                    error = (
                        exc
                        if isinstance(exc, Exception)
                        else RuntimeError(str(exc))
                    )
                # Release the facade before callbacks are queued so a closure
                # may start the next operation directly.
                with self._lock:
                    self._async_connect_active = False
                try:
                    if error is not None:
                        call._fail(error)
                    elif device is not None:
                        call._complete(device)
                finally:
                    with self._lock:
                        if self._async_connect is call:
                            self._async_connect = None
                            self._async_connect_thread = None

            thread = threading.Thread(
                target=run_connect,
                name="serialforge-connect",
                daemon=False,
            )
            self._async_connect_thread = thread
        try:
            thread.start()
        except BaseException:
            with self._lock:
                self._async_connect = None
                self._async_connect_active = False
                self._async_connect_thread = None
            raise
        return call

    def _connect_request(
        self,
        target: str | DeviceInfo | None,
        *,
        cancel_event: threading.Event | None = None,
    ) -> DeviceInfo:
        """Run one blocking connection after reserving the facade lifecycle."""
        async_worker = threading.current_thread() is self._async_connect_thread
        with self._lock:
            core = self._require_core()
            if (
                self._scanning
                or self._connecting
                or self._async_scan_active
                or (self._async_connect_active and not async_worker)
            ):
                raise ConfigError("scan or connection is already active")
            self._connecting = True
        if cancel_event is not None and cancel_event.is_set():
            with self._lock:
                self._connecting = False
            raise SerialForgeError("connection attempt was cancelled")
        try:
            self.disconnect()
            if cancel_event is not None and cancel_event.is_set():
                raise SerialForgeError("connection attempt was cancelled")
            core.connect(target)
        finally:
            with self._lock:
                self._connecting = False
        device = getattr(core._worker, "_device", None)
        if isinstance(device, DeviceInfo):
            return device
        if isinstance(target, DeviceInfo):
            return target
        raise SerialForgeError("connection completed without a device")

    def _cancel_pending_connect(self) -> None:
        """Stop a pending connection attempt from another thread."""
        core = self._core
        if core is not None:
            core.disconnect()

        # ty: ignore[invalid-method-override, missing-override-decorator]

    def disconnect(self) -> None:
        """Cancel scans and commands and join serial workers; safely
        repeatable.
        """
        current = threading.current_thread()
        with self._lock:
            scan_call = self._async_scan
            connect_call = self._async_connect
        self.cancel_scan()
        self._scan_done.wait()
        if current is not self._async_thread and scan_call is not None:
            scan_call.cancel()
            scan_call._done_event.wait()
        if (
            connect_call is not None
            and current is not self._async_connect_thread
        ):
            connect_call.cancel()
        with self._lock:
            calls = tuple(self._callback_calls)
        for call in calls:
            call.cancel()
        core = self._core
        if core is not None:
            core.disconnect()
        if (
            connect_call is not None
            and current is not self._async_connect_thread
        ):
            connect_call._done_event.wait()
        with self._lock:
            self._receive_splitters.clear()

    def _require_core(self) -> HandlerCore:
        """Reject operations that require an unset profile."""
        if self._core is None:
            raise ConfigError("configure the device before this operation")
        return self._core

    @overload
    def send_async(
        self,
        target: Spec[T],
        /,
        *,
        timeout_s: float = 3.0,
        priority: CommandPriority = CommandPriority.NORMAL,
        params: Mapping[str, object] = _EMPTY,
        context: Mapping[str, object] = _EMPTY,
    ) -> CommandCall[T]: ...

    @overload
    def send_async(
        self,
        target: CommandSpec | str,
        /,
        *,
        timeout_s: float = 3.0,
        priority: CommandPriority = CommandPriority.NORMAL,
        params: Mapping[str, object] = _EMPTY,
        context: Mapping[str, object] = _EMPTY,
    ) -> CommandCall[Any]: ...

    def send_async(
        self,
        target: CommandSpec | str,
        /,
        *,
        timeout_s: float = 3.0,
        priority: CommandPriority = CommandPriority.NORMAL,
        params: Mapping[str, object] = _EMPTY,
        context: Mapping[str, object] = _EMPTY,
    ) -> CommandCall[Any]:
        """Submit without waiting; the deadline includes queue and UI delivery.

        Pass wire placeholders in params and application metadata in context.
        The returned handle accepts add_done_callback(callback) closures taking
        one Message. Failures are retained and raised by handle.result().
        """
        if (
            type(timeout_s) not in (int, float)
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
        ):
            raise CommandError("timeout_s must be finite positive seconds")
        if not isinstance(priority, CommandPriority):
            raise CommandError("priority must be CommandPriority")
        if isinstance(target, Spec) and target.role is SpecRole.EVENT:
            raise CommandError("cannot send an event declaration")
        if not isinstance(params, Mapping) or not isinstance(context, Mapping):
            raise CommandError("params and context must be mappings")
        deadline = time.monotonic() + timeout_s
        snapshot = _freeze_context(context)
        with self._lock:
            core = self._require_core()
            self._submitting += 1

        def factory(
            request_id: str, spec: CommandSpec | None, route: SendRoute
        ) -> CommandCall[Any]:
            call = CommandCall(
                request_id,
                spec,
                route,
                context=snapshot,
                timeout_s=timeout_s,
                deadline=deadline,
                dispatch=self._dispatch,
                cancel=core._dispatcher.cancel_ticket,
            )
            with self._lock:
                self._calls[request_id] = call
                self._callback_calls.add(call)
            return call

        try:
            ticket = core._dispatcher.submit(
                target,
                priority=priority,
                allow_raw_text=self._allow_raw_text,
                _params=dict(params),
                _context=snapshot,
                _ticket_factory=factory,
            )
        finally:
            with self._lock:
                self._submitting -= 1
        assert isinstance(ticket, CommandCall)
        ticket._start_timer()
        return ticket

    @overload
    def send_sync(
        self,
        target: Spec[T],
        /,
        *,
        wait_timeout_s: float = 3.0,
        priority: CommandPriority = CommandPriority.NORMAL,
        params: Mapping[str, object] = _EMPTY,
        context: Mapping[str, object] = _EMPTY,
    ) -> Message[T]: ...

    @overload
    def send_sync(
        self,
        target: CommandSpec | str,
        /,
        *,
        wait_timeout_s: float = 3.0,
        priority: CommandPriority = CommandPriority.NORMAL,
        params: Mapping[str, object] = _EMPTY,
        context: Mapping[str, object] = _EMPTY,
    ) -> Message[Any]: ...

    def send_sync(
        self,
        target: CommandSpec | str,
        /,
        *,
        wait_timeout_s: float = 3.0,
        priority: CommandPriority = CommandPriority.NORMAL,
        params: Mapping[str, object] = _EMPTY,
        context: Mapping[str, object] = _EMPTY,
    ) -> Message[Any]:
        """Block for the shared result; execution failures raise typed errors.

        STREAM is rejected before submission. Calling in the main thread blocks
        the UI; use an application worker when a GUI must remain responsive.
        """
        core = self._require_core()
        if core._worker is not None and (
            QThread.currentThread() == core._worker
            or QThread.currentThread() == core._worker._reader
        ):
            raise CommandError(
                "send_sync cannot run on a private serial I/O thread"
            )
        resolved = (
            target
            if isinstance(target, CommandSpec)
            else self._registry.resolve_text(target, core._profile.terminator)
        )
        spec = resolved[0] if isinstance(resolved, tuple) else resolved
        if spec is not None and spec.mode is ResponseMode.STREAM:
            raise CommandError("send_sync does not support STREAM")
        call = self.send_async(
            target,
            timeout_s=wait_timeout_s,
            priority=priority,
            params=params,
            context=context,
        )
        if not call._done_event.wait(wait_timeout_s):
            call._expire()
        return call.result()

    def set_init_sequence(self, items: Sequence[CommandSpec | str]) -> None:
        """Set registered initialization commands run on open and reconnect."""
        self._require_core().set_init_sequence(list(items))

    def set_log_config(self, config: LogConfig) -> None:
        """Change private logging policy without adding public log signals."""
        self._log = config
        if self._core is not None:
            self._core.set_log_config(config)

    def stats(self) -> HandlerStats:
        """Return immutable transport and dropped-background-error counters."""
        stats = self._core.stats() if self._core else HandlerStats()
        return replace(stats, dropped_background_errors=self._dropped_errors)

    def check_errors(self) -> None:
        """Pop and raise the oldest unowned background error, if any."""
        with self._lock:
            if self._errors:
                raise self._errors.popleft()


__all__ = ["SerialForge"]
