"""Incremental byte framing for text and binary serial protocols."""

from __future__ import annotations

import time
from collections.abc import Callable

from serialforge.advanced import FramingConfig
from serialforge.enums import FramingMode
from serialforge.errors import FramingError


class FrameSplitter:
    """Split arbitrary byte chunks into complete frames without decoding."""

    _config: FramingConfig
    _buffer: bytearray
    _last_byte_at: float | None

    def __init__(
        self,
        config: FramingConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a splitter with bounded buffering."""
        self._config = config or FramingConfig()
        self._buffer = bytearray()
        self._last_byte_at = None
        self._clock: Callable[[], float] = clock

    def feed(self, data: bytes) -> list[bytes]:
        """Append bytes and return every complete frame now available."""
        if data:
            self._buffer.extend(data)
            self._last_byte_at = self._clock()
        if len(self._buffer) > self._config.max_buffer_bytes:
            self._resynchronise()
            raise FramingError("receive buffer exceeded max_buffer_bytes")
        if self._config.mode is FramingMode.LINE:
            return self.split_terminator(self._config.terminator)
        if self._config.mode is FramingMode.DELIMITED:
            return self.split_delimited()
        if self._config.mode is FramingMode.LENGTH_PREFIX:
            return self.split_length_prefix()
        return self.split_silence_gap()

    def flush(self, *, now: float | None = None) -> list[bytes]:
        """Emit a silence-gap frame after the configured idle period."""
        if self._config.mode is not FramingMode.SILENCE_GAP:
            return []
        if not self._buffer or self._last_byte_at is None:
            return []
        current = self._clock() if now is None else now
        gap = self._config.silence_gap_s
        # FramingConfig validates SILENCE_GAP at construction.
        assert gap is not None
        if current - self._last_byte_at < gap:
            return []
        frame = bytes(self._buffer)
        self._buffer.clear()
        return [self.limit_frame(frame)]

    def split_terminator(self, terminator: bytes) -> list[bytes]:
        """Split line-style frames, preserving no terminator bytes."""
        frames: list[bytes] = []
        while terminator:
            index = self._buffer.find(terminator)
            if index < 0:
                break
            frames.append(self.limit_frame(bytes(self._buffer[:index])))
            del self._buffer[: index + len(terminator)]
        return frames

    def split_delimited(self) -> list[bytes]:
        """Split frames enclosed by start and end markers."""
        start = self._config.start_marker
        end = self._config.end_marker
        frames: list[bytes] = []
        while start and end:
            begin = self._buffer.find(start)
            if begin < 0:
                self._resynchronise()
                break
            if begin:
                del self._buffer[:begin]
            finish = self._buffer.find(end, len(start))
            if finish < 0:
                break
            stop = finish + len(end)
            frames.append(self.limit_frame(bytes(self._buffer[:stop])))
            del self._buffer[:stop]
        return frames

    def split_length_prefix(self) -> list[bytes]:
        """Split frames whose length is encoded in the header."""
        offset = self._config.length_offset
        width = self._config.length_size
        frames: list[bytes] = []
        while len(self._buffer) >= offset + width:
            length_start = offset
            length_end = length_start + width
            payload_length = int.from_bytes(
                self._buffer[length_start:length_end],
                "little" if self._config.byteorder == "little" else "big",
            )
            frame_length = length_end + payload_length
            if frame_length > self._config.max_frame_length:
                self._resynchronise()
                raise FramingError("frame exceeded max_frame_length")
            if len(self._buffer) < frame_length:
                break
            frames.append(self.limit_frame(bytes(self._buffer[:frame_length])))
            del self._buffer[:frame_length]
        return frames

    def split_silence_gap(self) -> list[bytes]:
        """Return no frames until ``flush`` observes a gap."""
        return []

    def limit_frame(self, frame: bytes) -> bytes:
        """Validate one frame against the configured maximum."""
        if len(frame) > self._config.max_frame_length:
            raise FramingError("frame exceeded max_frame_length")
        return frame

    def _resynchronise(self) -> None:
        """Drop bounded garbage while retaining possible marker prefixes."""
        limit = self._config.max_frame_length
        if len(self._buffer) > limit:
            del self._buffer[:-limit]


__all__ = ["FrameSplitter"]
