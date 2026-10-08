"""Probe candidate baudrates with bounded reads and byte framing."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from loguru import logger

from ..advanced import FramingConfig, ProbeSpec, SerialConfig
from ..enums import FramingMode
from ..errors import FramingError, PortBusyError
from ..models import CommandSpec, DeviceInfo, DeviceProfile
from ..protocols import PortBackend, TransportProtocol
from ..settings import LOG_ENABLED
from ..transport import FrameSplitter, PortOptions, PortRegistry
from .baud_cache import BaudCache

# Probe state is deliberately kept together while each rate owns one handle.
# pylint: disable=too-many-locals


class BaudProber:
    """Try allowed baudrates serially on one port and validate its reply."""

    def __init__(
        self, profile: DeviceProfile, backend: PortBackend, cache: BaudCache
    ) -> None:
        """Retain the profile, backend, and per-device baud hint cache."""
        self._profile: DeviceProfile = profile
        self._backend: PortBackend = backend
        self._cache: BaudCache = cache

    def probe(
        self,
        device: DeviceInfo,
        cancel: threading.Event,
        deadline: float,
        progress: Callable[[str, int], None],
    ) -> DeviceInfo | None:
        """Return the first validated baudrate or no result for this port."""
        started = time.monotonic()
        cached = self._cache.get(device)
        rates = list(self._profile.baudrates)
        if cached in rates:
            rates.remove(cached)
            rates.insert(0, cached)
        for baudrate in rates:
            if cancel.is_set() or time.monotonic() >= deadline:
                return None
            progress(device.port, baudrate)
            try:
                response = self.try_rate(device, baudrate, cancel, deadline)
            except (OSError, RuntimeError, FramingError, PortBusyError) as exc:
                if LOG_ENABLED:
                    logger.debug(
                        "probe failed on {} at {}: {}",
                        device.port,
                        baudrate,
                        exc,
                    )
                if isinstance(exc, PortBusyError):
                    return None
                continue
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

    def try_rate(
        self,
        device: DeviceInfo,
        baudrate: int,
        cancel: threading.Event,
        total_deadline: float,
    ) -> bytes | None:
        """Open, probe, and always close one baudrate attempt."""
        config = self._profile.serial
        assert isinstance(config, SerialConfig)
        owner = object()
        with PortRegistry.lease(device.port, owner):
            transport = self._backend.open(
                device.port, PortOptions.build(config, baudrate)
            )
            try:
                transport.set_control_lines(dtr=config.dtr, rts=config.rts)
                if config.rx_buffer_size is not None:
                    transport.set_buffer_size(config.rx_buffer_size)
                probe = self._profile.probe
                assert isinstance(probe, (CommandSpec, ProbeSpec))
                settle = (
                    probe.settle_time_s
                    if isinstance(probe, ProbeSpec)
                    and probe.settle_time_s is not None
                    else config.settle_time_s
                )
                if cancel.wait(
                    min(settle, max(0.0, total_deadline - time.monotonic()))
                ):
                    return None
                if time.monotonic() >= total_deadline:
                    return None
                transport.reset_input_buffer()
                request = (
                    probe.request.rstrip("\r\n") + self._profile.terminator
                ).encode(self._profile.encoding)
                if transport.write(request) != len(request):
                    return None
                timeout = probe.timeout_s
                seconds = (
                    float(timeout) if isinstance(timeout, (int, float)) else 1.0
                )
                deadline = min(total_deadline, time.monotonic() + seconds)
                return self.read_reply(transport, request, cancel, deadline)
            finally:
                try:
                    transport.close()
                except (OSError, RuntimeError) as exc:
                    if LOG_ENABLED:
                        logger.debug(
                            "probe close failed on {}: {}", device.port, exc
                        )

    def read_reply(
        self,
        transport: TransportProtocol,
        request: bytes,
        cancel: threading.Event,
        deadline: float,
    ) -> bytes | None:
        """Validate framed responses until success, cancellation, or timeout."""
        profile = self._profile
        framing = profile.framing or FramingConfig(
            mode=FramingMode.LINE,
            terminator=profile.terminator.encode(profile.encoding),
        )
        splitter = FrameSplitter(framing)
        sent_text = request.decode(profile.encoding, errors="replace").strip()
        while not cancel.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            chunk = transport.read(1, min(0.02, remaining))
            if cancel.is_set():
                return None
            frames: list[bytes] = []
            if chunk:
                parts = [chunk]
                while True:
                    extra = transport.read(4096, 0.0)
                    if not extra:
                        break
                    parts.append(extra)
                frames.extend(splitter.feed(b"".join(parts)))
            frames.extend(splitter.flush())
            for frame in frames:
                text = frame.decode(profile.encoding, errors="replace")
                if text == sent_text:
                    continue
                if self.matches(text):
                    return frame
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


__all__ = ["BaudProber"]
