"""Checksum calculation and verification for binary frames."""

from __future__ import annotations

import binascii
from collections.abc import Callable

from serialforge.enums import Checksum


class ChecksumCalculator:
    """Calculate or verify supported checksums with a stable byte order."""

    # The explicit branches keep algorithm-specific byte order visible.
    # pylint: disable=too-many-return-statements

    @staticmethod
    def calculate(
        data: bytes,
        algorithm: Checksum,
        custom: Callable[[bytes], bytes] | None = None,
    ) -> bytes:
        """Return the checksum bytes for ``data``."""
        if custom is not None:
            return bytes(custom(data))
        if algorithm is Checksum.NONE:
            return b""
        if algorithm is Checksum.SUM8:
            return bytes((sum(data) & 0xFF,))
        if algorithm is Checksum.XOR:
            value = 0
            for item in data:
                value ^= item
            return bytes((value,))
        if algorithm is Checksum.CRC16_MODBUS:
            return ChecksumCalculator.crc16(data, 0xA001, 0xFFFF)
        if algorithm is Checksum.CRC16_CCITT:
            return ChecksumCalculator.crc16_ccitt(data)
        if algorithm is Checksum.CRC32:
            return (binascii.crc32(data) & 0xFFFFFFFF).to_bytes(4, "little")
        raise ValueError(f"unsupported checksum algorithm: {algorithm}")

    @staticmethod
    def verify(
        data: bytes,
        expected: bytes,
        algorithm: Checksum,
        custom: Callable[[bytes], bytes] | None = None,
    ) -> bool:
        """Return whether ``expected`` matches the calculated checksum."""
        return ChecksumCalculator.calculate(data, algorithm, custom) == expected

    @staticmethod
    def crc16(data: bytes, polynomial: int, initial: int) -> bytes:
        """Calculate reflected CRC-16 and return little-endian bytes."""
        value = initial
        for item in data:
            value ^= item
            for _ in range(8):
                value = (value >> 1) ^ polynomial if value & 1 else value >> 1
        return value.to_bytes(2, "little")

    @staticmethod
    def crc16_ccitt(data: bytes) -> bytes:
        """Calculate CRC-16/CCITT-FALSE and return big-endian bytes."""
        value = 0xFFFF
        for item in data:
            value ^= item << 8
            for _ in range(8):
                if value & 0x8000:
                    value = ((value << 1) ^ 0x1021) & 0xFFFF
                else:
                    value = (value << 1) & 0xFFFF
        return value.to_bytes(2, "big")


__all__ = ["ChecksumCalculator"]
