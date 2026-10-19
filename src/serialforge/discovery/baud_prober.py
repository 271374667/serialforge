"""Probe candidate baudrates on one open handle with bounded I/O."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from loguru import logger

from serialforge.advanced import (
    FramingConfig,
    ProbeSpec,
    RuntimeConfig,
    SerialConfig,
)
from serialforge.discovery.baud_cache import BaudCache
from serialforge.enums import FramingMode
from serialforge.errors import FramingError, PortBusyError
from serialforge.models import CommandSpec, DeviceInfo, DeviceProfile
from serialforge.protocols import PortBackend, TransportProtocol
from serialforge.settings import LOG_ENABLED
from serialforge.transport import FrameSplitter, PortOptions, PortRegistry

# Probe state is deliberately kept together while one port owns one handle.
# pylint: disable=too-many-locals,too-many-branches,too-many-statements


class BaudProber:
    """Try allowed baudrates on one open handle and validate its reply.

    A port's open call runs in a helper thread bounded by
    ``probe_open_timeout_s`` so a stuck driver cannot delay discovery; a
    single handle then serves every candidate rate.  Unusable ports and rates
    are skipped rather than reported: discovery is a probe interface, so only
    invalid configuration or misuse may raise.
    """

    # Probe observers are injected by the facade.
    # pylint: disable=too-many-arguments
    def __init__(
        self,
        profile: DeviceProfile,
        backend: PortBackend,
        cache: BaudCache,
        *,
        raw_received: Callable[[bytes], None] | None = None,
        raw_sent: Callable[[bytes], None] | None = None,
    ) -> None:
        """Retain the profile, backend, and per-device baud hint cache."""
        self._profile: DeviceProfile = profile
        self._backend: PortBackend = backend
        self._cache: BaudCache = cache
        self._raw_received = raw_received
        self._raw_sent = raw_sent

    def probe(
        self,
        device: DeviceInfo,
        cancel: threading.Event,
        deadline: float,
        progress: Callable[[str, int], None],
    ) -> DeviceInfo | None:
        """Return the first validated baudrate or no result for this port.

        The port is opened once with a bounded budget and then reused for
        every rate; a rate without any reply byte is screened out early.
        """
        started = time.monotonic()
        rates = list(self._profile.baudrates)
        cached = self._cache.get(device)
        if cached in rates:
            rates.remove(cached)
            rates.insert(0, cached)
        config = self._profile.serial
        assert isinstance(config, SerialConfig)
        runtime = self._profile.runtime
        assert isinstance(runtime, RuntimeConfig)
        probe = self._profile.probe
        assert isinstance(probe, (CommandSpec, ProbeSpec))
        remaining = deadline - started
        if cancel.is_set() or remaining <= 0 or not rates:
            return None

        owner = object()
        try:
            PortRegistry.acquire(device.port, owner)
        except PortBusyError as exc:
            self._log_skip(device.port, rates[0], exc)
            return None
        done = threading.Event()
        lock = threading.Lock()
        abandoned = False
        opened: TransportProtocol | None = None
        open_error: Exception | None = None

        def open_worker() -> None:
            nonlocal opened, open_error
            try:
                opened = self._backend.open(
                    device.port, PortOptions.build(config, rates[0])
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                open_error = exc
            with lock:
                keep = not abandoned
                if keep:
                    done.set()
            if keep:
                return
            if open_error is not None:
                self._log_skip(device.port, rates[0], open_error)
            if opened is not None:
                self._close_quietly(opened)
            PortRegistry.release(device.port, owner)

        threading.Thread(
            target=open_worker,
            name=f"serialforge-open-{device.port}",
            daemon=True,
        ).start()
        settled = done.wait(min(runtime.probe_open_timeout_s, remaining))
        with lock:
            give_up = not settled or (opened is None and open_error is None)
            abandoned = give_up
        if give_up:
            self._log_skip(
                device.port,
                rates[0],
                TimeoutError("open exceeded probe_open_timeout_s"),
            )
            return None
        if open_error is not None:
            PortRegistry.release(device.port, owner)
            self._log_skip(device.port, rates[0], open_error)
            return None
        transport = opened
        assert transport is not None
        try:
            return self.probe_handle(
                device,
                transport,
                rates,
                probe,
                cancel,
                deadline,
                progress,
                started,
            )
        finally:
            self._close_quietly(transport)
            PortRegistry.release(device.port, owner)

    def probe_handle(
        self,
        device: DeviceInfo,
        transport: TransportProtocol,
        rates: list[int],
        probe: CommandSpec | ProbeSpec,
        cancel: threading.Event,
        deadline: float,
        progress: Callable[[str, int], None],
        started: float,
    ) -> DeviceInfo | None:
        """Screen and confirm candidate rates on one already open handle."""
        config = self._profile.serial
        assert isinstance(config, SerialConfig)
        runtime = self._profile.runtime
        assert isinstance(runtime, RuntimeConfig)
        settle = (
            probe.settle_time_s
            if isinstance(probe, ProbeSpec) and probe.settle_time_s is not None
            else config.settle_time_s
        )
        try:
            transport.set_control_lines(dtr=config.dtr, rts=config.rts)
            if config.rx_buffer_size is not None:
                transport.set_buffer_size(config.rx_buffer_size)
        except (OSError, RuntimeError) as exc:
            self._log_skip(device.port, rates[0], exc)
            return None
        if cancel.wait(min(settle, max(0.0, deadline - time.monotonic()))):
            return None
        if time.monotonic() >= deadline:
            return None
        activity = runtime.probe_activity_window_s
        for index, baudrate in enumerate(rates):
            if cancel.is_set() or time.monotonic() >= deadline:
                return None
            progress(device.port, baudrate)
            try:
                if index:
                    transport.set_baudrate(baudrate)
                transport.reset_input_buffer()
                request = (
                    probe.request.rstrip("\r\n") + self._profile.terminator
                ).encode(self._profile.encoding)
                written = transport.write(request)
                if written > 0 and self._raw_sent is not None:
                    self._raw_sent(request[:written])
                if written != len(request):
                    return None
                timeout = probe.timeout_s
                seconds = (
                    float(timeout) if isinstance(timeout, (int, float)) else 1.0
                )
                rate_deadline = min(deadline, time.monotonic() + seconds)
                response = self.read_reply(
                    transport,
                    request,
                    cancel,
                    rate_deadline,
                    activity,
                )
            except (OSError, RuntimeError, FramingError, PortBusyError) as exc:
                self._log_skip(device.port, baudrate, exc)
                return None
            if response is not None:
                result = replace(
                    device,
                    baudrate=baudrate,
                    response=response,
                    elapsed_s=time.monotonic() - started,
                )
                self._cache.remember(result, baudrate)
                return result
        return None

    def read_reply(
        self,
        transport: TransportProtocol,
        request: bytes,
        cancel: threading.Event,
        deadline: float,
        activity_window: float = 0.0,
    ) -> bytes | None:
        """Validate framed responses until success, cancellation or timeout.

        A non-zero activity window ends one rate early when no byte other
        than the echoed request arrived.  Only protocols that answer at any
        baudrate turn that into a speedup; a silent rate stops paying the
        full probe timeout.
        """
        profile = self._profile
        framing = profile.framing or FramingConfig(
            mode=FramingMode.LINE,
            terminator=profile.terminator.encode(profile.encoding),
        )
        splitter = FrameSplitter(framing)
        sent_text = request.decode(profile.encoding, errors="replace").strip()
        sent_bytes = request.strip()
        observed = b""
        active = False
        window_end = (
            deadline
            if activity_window <= 0
            else min(deadline, time.monotonic() + activity_window)
        )
        while not cancel.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            chunk = transport.read(1, min(0.02, remaining))
            if chunk and self._raw_received is not None:
                self._raw_received(chunk)
            if cancel.is_set():
                return None
            frames: list[bytes] = []
            if chunk:
                parts = [chunk]
                observed += chunk
                while True:
                    extra = transport.read(4096, 0.0)
                    if not extra:
                        break
                    observed += extra
                    if self._raw_received is not None:
                        self._raw_received(extra)
                    parts.append(extra)
                active = active or observed.strip() != sent_bytes
                frames.extend(splitter.feed(b"".join(parts)))
            frames.extend(splitter.flush())
            for frame in frames:
                text = frame.decode(profile.encoding, errors="replace")
                if text == sent_text:
                    continue
                if self.matches(text):
                    return frame
            if not active and time.monotonic() >= window_end:
                return None
        return None

    def matches(self, text: str) -> bool:
        """Require the probe's regex or predicate, never mere activity."""
        probe = self._profile.probe
        if isinstance(probe, CommandSpec):
            return (
                probe.pattern is not None
                and re.fullmatch(probe.pattern, text) is not None
            )
        assert isinstance(probe, ProbeSpec)
        if isinstance(probe.expect, str):
            return re.fullmatch(probe.expect, text) is not None
        return bool(probe.expect(text))

    def _close_quietly(self, transport: TransportProtocol) -> None:
        """Close one handle without hiding failures from the scan."""
        try:
            transport.close()
        except (OSError, RuntimeError) as exc:
            if LOG_ENABLED:
                logger.debug("probe close failed: {}", exc)

    def _log_skip(self, port: str, baudrate: int, error: BaseException) -> None:
        """Record one skipped candidate without raising to the caller."""
        if LOG_ENABLED:
            logger.debug("probe skipped on {} at {}: {}", port, baudrate, error)


__all__ = ["BaudProber"]
