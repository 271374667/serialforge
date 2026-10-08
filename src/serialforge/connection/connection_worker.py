"""QtCore connection worker for command pumping and automatic recovery."""

from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from typing_extensions import override

# pylint: disable=no-name-in-module
from PySide6.QtCore import Qt, QThread

from ..advanced import FramingConfig, ProbeSpec, RuntimeConfig, SerialConfig
from ..discovery import DeviceFinder
from ..discovery.port_scanner import PortScanner
from ..enums import (
    CommandPriority,
    CommandStatus,
    ConnectionState,
    FramingMode,
    ScanMode,
)
from ..errors import PortNotFoundError, SerialForgeError
from ..models import CommandResult, CommandSpec, DeviceInfo
from ..protocols import TransportProtocol
from ..transport import BackendSwitch, PortOptions, PortRegistry
from .read_thread import ReadThread

# The worker coordinates the facade's private state across Qt threads.
# pylint: disable=protected-access,too-many-instance-attributes

if TYPE_CHECKING:
    from .serial_handler import SerialHandler


class ConnectionWorker(QThread):
    """Own connection attempts, command pumping, and automatic recovery."""

    def __init__(
        self, handler: SerialHandler, target: str | DeviceInfo | None
    ) -> None:
        """Create a stopped worker for one explicit connection session."""
        super().__init__()
        self._handler: SerialHandler = handler
        self._target: str | DeviceInfo | None = target
        self._stop: threading.Event = threading.Event()
        self._io_failed: threading.Event = threading.Event()
        self._transport: TransportProtocol | None = None
        self._reader: ReadThread | None = None
        self._device: DeviceInfo | None = None
        self._port: str | None = None
        self._reconnects: int = 0
        self._session_started: bool = False
        self._finder: DeviceFinder | None = None

    @property
    def port(self) -> str | None:
        """Return the port currently held by this worker."""
        return self._port

    def request_stop(self) -> None:
        """Stop reads, writes, retries, and future command pumping."""
        self._stop.set()
        finder = self._finder
        if finder is not None:
            finder.cancel()
        reader = self._reader
        if reader is not None:
            reader.request_stop()
        transport = self._transport
        if transport is not None:
            transport.cancel_read()
            transport.cancel_write()

    def write(self, data: bytes) -> int:
        """Write through the current transport and report a broken handle."""
        transport = self._transport
        if transport is None or self._stop.is_set():
            raise RuntimeError("serial transport is not connected")
        try:
            written = transport.write(data)
            if written != len(data):
                self._io_failed.set()
            return written
        except (OSError, RuntimeError):
            self._io_failed.set()
            raise

    def clear_input(self) -> None:
        """Discard stale bytes after an exclusive timeout."""
        transport = self._transport
        if transport is not None:
            transport.reset_input_buffer()

    @override
    def run(self) -> None:
        """Open once, recover unexpected loss, and close every handle."""
        runtime = self._handler._profile.runtime
        assert isinstance(runtime, RuntimeConfig)
        attempts = 0
        first = True
        try:
            while not self._stop.is_set():
                try:
                    device = self.resolve_target(reconnect=not first)
                    if self._stop.is_set():
                        break
                    self.open_device(device)
                    if self._stop.is_set():
                        break
                    attempts = 0
                    self.run_connected()
                    first = False
                    if self._stop.is_set():
                        break
                    self._handler._transition(ConnectionState.RECONNECTING)
                except (OSError, RuntimeError, SerialForgeError) as exc:
                    if not self._stop.is_set():
                        self._handler._report_error(exc)
                        if first:
                            self._handler._transition(ConnectionState.FAILED)
                            return
                        self._handler._transition(ConnectionState.RECONNECTING)
                finally:
                    self.close_transport()
                if self._stop.is_set():
                    break
                attempts += 1
                if (
                    runtime.reconnect_max_attempts is not None
                    and attempts > runtime.reconnect_max_attempts
                ):
                    self._handler._transition(ConnectionState.FAILED)
                    return
                delay = min(
                    runtime.reconnect_initial_s * 2 ** (attempts - 1),
                    runtime.reconnect_max_s,
                )
                self._stop.wait(delay * random.uniform(0.9, 1.1))
        finally:
            self.close_transport()
            self._handler._traffic.end_connection()
            if self._stop.is_set():
                self._handler._transition(ConnectionState.DISCONNECTED)

    def resolve_target(self, *, reconnect: bool) -> DeviceInfo:
        """Resolve an explicit target or ask discovery for a matching device."""
        if reconnect and self._device is not None:
            previous = self._device
            candidates = PortScanner(
                self._handler._profile, BackendSwitch.current()
            ).scan()
            for candidate in candidates:
                same_serial = bool(previous.serial_number) and (
                    previous.serial_number == candidate.serial_number
                )
                same_location = bool(previous.location) and (
                    previous.location == candidate.location
                )
                if same_serial or same_location:
                    return replace(candidate, baudrate=previous.baudrate)
                if (
                    not previous.serial_number
                    and not previous.location
                    and candidate.port == previous.port
                ):
                    return replace(candidate, baudrate=previous.baudrate)
            runtime = self._handler._profile.runtime
            assert isinstance(runtime, RuntimeConfig)
            if not runtime.reprobe_on_reconnect:
                return previous
        target = None if reconnect else self._target
        if isinstance(target, DeviceInfo):
            return target
        self._handler._transition(ConnectionState.PROBING)
        finder = DeviceFinder(self._handler._profile)
        self._finder = finder
        try:
            if self._stop.is_set():
                raise PortNotFoundError("device discovery was cancelled")
            results = finder.find(ScanMode.FIRST_MATCH, port=target)
        finally:
            self._finder = None
        if not results:
            raise PortNotFoundError(
                f"no matching serial device found on {target or 'available ports'}"
            )
        return results[0]

    def open_device(self, device: DeviceInfo) -> None:
        """Claim and open a port, then start its independent read thread."""
        profile = self._handler._profile
        serial_config = profile.serial
        assert isinstance(serial_config, SerialConfig)
        baudrate = device.baudrate or profile.baudrates[0]
        self._handler._transition(ConnectionState.CONNECTING)
        PortRegistry.acquire(device.port, self._handler)
        self._port = device.port
        transport = BackendSwitch.current().open(
            device.port, PortOptions.build(serial_config, baudrate)
        )
        self._transport = transport
        transport.set_control_lines(
            dtr=serial_config.dtr, rts=serial_config.rts
        )
        if serial_config.rx_buffer_size is not None:
            transport.set_buffer_size(serial_config.rx_buffer_size)
        if self._stop.wait(serial_config.settle_time_s):
            return
        framing = profile.framing or FramingConfig(
            mode=FramingMode.LINE,
            terminator=profile.terminator.encode(profile.encoding),
        )
        self._device = device
        self._io_failed.clear()
        self._handler._dispatcher.set_clear_input(self.clear_input)
        self._handler._dispatcher.set_connected(True)
        reader = ReadThread(self._handler, transport, framing)
        self._reader = reader
        reader.start()

    def run_connected(self) -> None:
        """Run initialization, publish state, and pump until loss or stop."""
        profile = self._handler._profile
        for item in self._handler._init_sequence:
            try:
                result = self._run_command(item)
            except SerialForgeError as exc:
                self._handler._report_error(exc)
                continue
            if result is None or result.status is not CommandStatus.OK:
                self._handler._report_error(
                    SerialForgeError(f"initialization command failed: {item!r}")
                )
            if self._stop.is_set() or self._reader_failed():
                return
        port = self._port
        assert port is not None
        if not self._session_started:
            self._handler._traffic.begin_connection(port)
            self._session_started = True
        else:
            self._reconnects += 1
            self._handler._traffic.record_event(
                port, f"RECONNECTED attempt={self._reconnects}"
            )
        self._handler._transition(ConnectionState.CONNECTED)
        last_heartbeat = time.monotonic()
        while not self._stop.is_set() and not self._reader_failed():
            self._handler._dispatcher.pump()
            self._handler._traffic.flush_stream_traffic()
            if (
                profile.heartbeat_s is not None
                and time.monotonic() - last_heartbeat >= profile.heartbeat_s
            ):
                if not self.check_heartbeat():
                    self._io_failed.set()
                    break
                last_heartbeat = time.monotonic()
            self._stop.wait(0.005)
        if not self._stop.is_set() and self._reader is not None:
            failure = self._reader.failure
            self._handler._report_error(
                failure or SerialForgeError("serial connection was lost")
            )

    def _reader_failed(self) -> bool:
        """Return whether either I/O direction can no longer be used."""
        reader = self._reader
        return self._io_failed.is_set() or (
            reader is not None and reader.failure is not None
        )

    def _run_command(self, item: CommandSpec | str) -> CommandResult | None:
        """Wait for one internal command while reads continue independently."""
        completed = threading.Event()
        received: list[CommandResult] = []

        def on_finished(result: CommandResult) -> None:
            received.append(result)
            completed.set()

        dispatcher = self._handler._dispatcher
        dispatcher.command_finished.connect(
            on_finished, Qt.ConnectionType.DirectConnection
        )
        try:
            ticket = dispatcher.submit(
                item, priority=CommandPriority.HIGH, allow_raw_text=False
            )
            while not self._stop.is_set():
                for result in tuple(received):
                    if result.request_id == ticket.request_id:
                        return result
                if self._reader_failed():
                    break
                completed.clear()
                dispatcher.pump()
                completed.wait(0.005)
            return None
        finally:
            dispatcher.command_finished.disconnect(on_finished)

    def check_heartbeat(self) -> bool:
        """Verify liveness using the configured probe declaration."""
        probe = self._handler._profile.probe
        if isinstance(probe, CommandSpec):
            if not self._handler._registry.contains(probe):
                self._handler._registry.register(probe)
            result = self._run_command(probe)
            return result is not None and result.status is CommandStatus.OK
        assert isinstance(probe, ProbeSpec)
        existing = self._handler._registry.resolve_text(
            probe.request, self._handler._profile.terminator
        )
        temporary = existing is None
        spec = (
            CommandSpec(
                probe.request,
                r"(?s)(?P<response>.*)",
                timeout_s=probe.timeout_s,
            )
            if temporary
            else existing[0]
        )
        if temporary:
            self._handler._registry.register(spec)
        try:
            result = self._run_command(spec)
        finally:
            if temporary:
                self._handler._registry.unregister(spec)
        if result is None or result.status is not CommandStatus.OK:
            return False
        frame = result.raw_frames[-1]
        response = (
            frame.decode(self._handler._profile.encoding, errors="replace")
            if isinstance(frame, bytes)
            else frame
        )
        if response == probe.request.strip():
            return False
        if isinstance(probe.expect, str):
            return re.fullmatch(probe.expect, response) is not None
        return bool(probe.expect(response))

    def close_transport(self) -> None:
        """Stop the reader, close the handle, and release ownership."""
        dispatcher = self._handler._dispatcher
        dispatcher.set_connected(
            False,
            user_initiated=self._stop.is_set(),
            reconnecting=(
                not self._stop.is_set()
                and self._handler.state is ConnectionState.RECONNECTING
            ),
        )
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.request_stop()
            reader.wait(2000)
        transport = self._transport
        self._transport = None
        if transport is not None:
            try:
                transport.close()
            except (OSError, RuntimeError) as exc:
                self._handler._report_error(exc)
        port = self._port
        self._port = None
        if port is not None:
            PortRegistry.release(port, self._handler)


__all__ = ["ConnectionWorker"]
