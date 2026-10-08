"""In-memory transport implementing the serial byte-stream contract."""

from __future__ import annotations

import time
from collections import deque
from threading import Condition

from .simulated_device import SimulatedDevice

# The fake handle mirrors the real transport's independent control state.
# pylint: disable=too-many-instance-attributes


class FakeTransport:
    """Connect a caller to one scriptable :class:`SimulatedDevice`."""

    def __init__(
        self, device: SimulatedDevice, baudrate: int | None = None
    ) -> None:
        """Create a closed-in-memory connection for ``device``."""
        self._device = device
        self._baudrate: int | None = baudrate
        self._incoming: deque[bytes] = deque()
        self._condition = Condition()
        self._closed = False
        self._cancelled_read = False
        self._cancelled_write = False
        self._write_count = 0

    @property
    def closed(self) -> bool:
        """Return whether the fake handle has been closed."""
        return self._closed

    @property
    def write_count(self) -> int:
        """Return the number of writes accepted by this handle."""
        return self._write_count

    def read(self, size: int, timeout_s: float) -> bytes:
        """Read up to ``size`` bytes, returning empty bytes on timeout."""
        if size <= 0:
            raise ValueError("size must be positive")
        deadline = time.monotonic() + max(0.0, timeout_s)
        with self._condition:
            while not self._incoming and not self._closed:
                if self._cancelled_read:
                    self._cancelled_read = False
                    return b""
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b""
                self._condition.wait(remaining)
            if self._closed:
                return b""
            data = self._incoming.popleft()
            if len(data) > size:
                self._incoming.appendleft(data[size:])
                return data[:size]
            return data

    def write(self, data: bytes) -> int:
        """Accept bytes and enqueue the scripted response chunks."""
        if self._closed:
            raise OSError("fake serial port is closed")
        if self._cancelled_write:
            self._cancelled_write = False
            return 0
        self._write_count += 1
        if (
            self._device.disconnect_after_writes is not None
            and self._write_count > self._device.disconnect_after_writes
        ):
            self.close()
            raise OSError("simulated disconnect")
        if self._device.latency_s > 0:
            time.sleep(self._device.latency_s)
        chunks = (
            self._device.response_for(bytes(data))
            if self._device.baudrate is None
            or self._device.baudrate == self._baudrate
            else [b"\x00"]
        )
        with self._condition:
            self._incoming.extend(chunks)
            self._condition.notify_all()
        return len(data)

    def inject(self, data: bytes) -> None:
        """Inject an unsolicited device frame."""
        with self._condition:
            self._incoming.append(bytes(data))
            self._condition.notify_all()

    def close(self) -> None:
        """Close the fake handle and wake blocked readers."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def cancel_read(self) -> None:
        """Wake a blocked read without closing the handle."""
        with self._condition:
            self._cancelled_read = True
            self._condition.notify_all()

    def cancel_write(self) -> None:
        """Cancel the next write operation."""
        self._cancelled_write = True

    def reset_input_buffer(self) -> None:
        """Discard pending fake response chunks."""
        with self._condition:
            self._incoming.clear()

    def set_buffer_size(self, rx_size: int) -> None:
        """Accept a simulated driver receive buffer request."""
        del rx_size

    def set_control_lines(
        self, *, dtr: bool | None = None, rts: bool | None = None
    ) -> None:
        """Accept explicit simulated control line settings."""
        del dtr, rts


__all__ = ["FakeTransport"]
