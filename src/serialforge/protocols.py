"""Protocols for replaceable serial backends.

Only the transport implementation imports pyserial.  Higher layers depend on
these small protocols so tests can inject fake ports without changing facade
constructors.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class PortBackend(Protocol):
    """Provide port enumeration and opening."""

    def list_ports(self) -> Sequence[Any]:
        """Return available port information."""

    def open(
        self, port: str, params: Mapping[str, object]
    ) -> TransportProtocol:
        """Open a port using the supplied serial parameters."""


class TransportProtocol(Protocol):
    """Describe the operations required by the transport layer."""

    def read(self, size: int, timeout_s: float) -> bytes:
        """Read up to ``size`` bytes."""

    def write(self, data: bytes) -> int:
        """Write bytes and return the number accepted."""

    def close(self) -> None:
        """Close the port."""

    def cancel_read(self) -> None:
        """Interrupt a blocked read."""

    def cancel_write(self) -> None:
        """Interrupt a blocked write."""
