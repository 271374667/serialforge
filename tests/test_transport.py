"""M2 transport, framing, timing, and fake-backend contracts."""

from __future__ import annotations

import pytest

from serialforge.advanced import FramingConfig, ReadLoopConfig
from serialforge.enums import Checksum, FramingMode
from serialforge.errors import ConfigError, FramingError, PortBusyError
from serialforge.testing import FakeBackend, SimulatedDevice, use_fake_backend
from serialforge.transport import (
    ChecksumCalculator,
    FrameSplitter,
    LatencyTracker,
    PortRegistry,
)


def test_line_splitter_handles_half_and_sticky_frames() -> None:
    splitter = FrameSplitter(FramingConfig(terminator=b"\r\n"))
    assert splitter.feed(b"A\r") == []
    assert splitter.feed(b"\nB\r\nC") == [b"A", b"B"]
    assert splitter.feed(b"\r\n") == [b"C"]


def test_delimited_splitter_resynchronises_garbage() -> None:
    config = FramingConfig(
        mode=FramingMode.DELIMITED,
        start_marker=b"<",
        end_marker=b">",
    )
    splitter = FrameSplitter(config)
    assert splitter.feed(b"garbage<one><two>") == [b"<one>", b"<two>"]


def test_length_prefix_and_silence_gap() -> None:
    length = FramingConfig(
        mode=FramingMode.LENGTH_PREFIX,
        length_offset=0,
        length_size=1,
        byteorder="little",
    )
    splitter = FrameSplitter(length)
    assert splitter.feed(b"\x03abc\x01z") == [b"\x03abc", b"\x01z"]

    now = [0.0]
    gap = FramingConfig(
        mode=FramingMode.SILENCE_GAP,
        silence_gap_s=0.1,
    )
    splitter = FrameSplitter(gap, clock=lambda: now[0])
    splitter.feed(b"abc")
    assert splitter.flush() == []
    now[0] = 0.1
    assert splitter.flush() == [b"abc"]


def test_silence_gap_joins_fragmented_reply_without_terminator() -> None:
    now = [0.0]
    splitter = FrameSplitter(
        FramingConfig(mode=FramingMode.SILENCE_GAP, silence_gap_s=0.08),
        clock=lambda: now[0],
    )
    assert splitter.feed(b"Software ver") == []
    now[0] = 0.04
    assert splitter.flush() == []
    assert splitter.feed(b"sion 1.75") == []
    now[0] = 0.119
    assert splitter.flush() == []
    now[0] = 0.121
    assert splitter.flush() == [b"Software version 1.75"]
    assert splitter.flush() == []


def test_silence_gap_requires_explicit_positive_interval() -> None:
    with pytest.raises(ConfigError, match="requires silence_gap_s"):
        FramingConfig(mode=FramingMode.SILENCE_GAP)
    with pytest.raises(ConfigError, match="silence_gap_s must be positive"):
        FramingConfig(mode=FramingMode.SILENCE_GAP, silence_gap_s=0)


def test_frame_buffer_limit_raises_and_keeps_bounded_state() -> None:
    splitter = FrameSplitter(
        FramingConfig(max_buffer_bytes=4, max_frame_length=4)
    )
    try:
        splitter.feed(b"12345")
    except FramingError:
        pass
    else:
        raise AssertionError("expected FramingError")


def test_checksum_algorithms_and_custom_function() -> None:
    data = b"123456789"
    assert ChecksumCalculator.calculate(data, Checksum.SUM8) == b"\xdd"
    assert ChecksumCalculator.calculate(data, Checksum.XOR) == b"\x31"
    assert ChecksumCalculator.calculate(
        data, Checksum.CRC16_MODBUS
    ) == bytes.fromhex("374b")
    assert ChecksumCalculator.calculate(
        data, Checksum.CRC16_CCITT
    ) == bytes.fromhex("29b1")
    crc = ChecksumCalculator.calculate(data, Checksum.CRC32)
    assert ChecksumCalculator.verify(data, crc, Checksum.CRC32)
    assert ChecksumCalculator.calculate(data, Checksum.NONE) == b""
    assert (
        ChecksumCalculator.calculate(data, Checksum.NONE, lambda _: b"x")
        == b"x"
    )


def test_latency_tracker_cold_start_ewma_and_reset() -> None:
    tracker = LatencyTracker(ReadLoopConfig(min_samples=2))
    assert tracker.poll_interval_s() == 0.05
    tracker.observe(0.2)
    assert tracker.stats().srtt_s == 0.2
    tracker.observe(0.1)
    assert 0.005 <= tracker.poll_interval_s() <= 0.1
    tracker.reset()
    assert tracker.stats().srtt_s is None


def test_port_registry_is_exclusive_and_releases() -> None:
    first = object()
    second = object()
    PortRegistry.clear()
    PortRegistry.acquire("SIM1", first)
    try:
        try:
            PortRegistry.acquire("SIM1", second)
        except PortBusyError:
            pass
        else:
            raise AssertionError("expected PortBusyError")
    finally:
        PortRegistry.release("SIM1", first)
    assert not PortRegistry.is_owned("SIM1")


def test_fake_backend_round_trip_and_chunking() -> None:
    device = SimulatedDevice(
        0x1234,
        0x5678,
        port="SIM2",
        responses={b"PING": b"PONG"},
        chunk_size=2,
    )
    backend = FakeBackend([device])
    with use_fake_backend(backend):
        assert backend.list_ports()[0].device == "SIM2"
        transport = backend.open("SIM2", {})
        assert transport.write(b"PING") == 4
        assert transport.read(2, 0.1) == b"PO"
        assert transport.read(2, 0.1) == b"NG"
        transport.cancel_read()
        assert transport.read(1, 0.1) == b""
        transport.close()
