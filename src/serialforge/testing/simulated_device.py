"""Scriptable device description used by the fake serial backend."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

ResponseScript = Callable[[bytes], bytes | Sequence[bytes] | None]


@dataclass
# pylint: disable=too-many-instance-attributes
class SimulatedDevice:
    """Describe a fake port and its deterministic wire behavior.

    ``responses`` maps request bytes to response chunks. ``script`` can
    provide dynamic behavior and takes precedence.
    """

    vid: int
    pid: int
    port: str = "SIM1"
    serial_number: str | None = None
    location: str | None = None
    description: str = "serialforge simulated device"
    responses: Mapping[bytes | str, bytes | Sequence[bytes]] = field(
        default_factory=dict
    )
    script: ResponseScript | None = None
    latency_s: float = 0.0
    chunk_size: int | None = None
    echo: bool = False
    disconnect_after_writes: int | None = None

    @property
    def device(self) -> str:
        """Return the pyserial-style port name."""
        return self.port

    def response_for(self, data: bytes) -> list[bytes]:
        """Return scripted response chunks for a transmitted request."""
        if self.script is not None:
            result = self.script(data)
        else:
            key = data.decode("utf-8", errors="replace")
            result = self.responses.get(data, self.responses.get(key))
        if result is None:
            chunks: list[bytes] = []
        elif isinstance(result, bytes):
            chunks = [result]
        else:
            chunks = [bytes(item) for item in result]
        if self.echo:
            chunks.insert(0, data)
        if self.chunk_size is None or self.chunk_size <= 0:
            return chunks
        split: list[bytes] = []
        for chunk in chunks:
            split.extend(
                chunk[index : index + self.chunk_size]
                for index in range(0, len(chunk), self.chunk_size)
            )
        return split


__all__ = ["SimulatedDevice", "ResponseScript"]
