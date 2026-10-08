"""Process-local exclusive ownership of serial ports."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

from serialforge.errors import PortBusyError


class PortRegistry:
    """Track port ownership with re-entrant, process-local leases."""

    _owners: dict[str, object] = {}
    _lock = RLock()

    @classmethod
    def acquire(cls, port: str, owner: object) -> None:
        """Claim ``port`` or raise ``PortBusyError``."""
        with cls._lock:
            current = cls._owners.get(port)
            if current is not None and current is not owner:
                raise PortBusyError(f"serial port is busy: {port}")
            cls._owners[port] = owner

    @classmethod
    def release(cls, port: str, owner: object) -> None:
        """Release a port only when owned by ``owner``."""
        with cls._lock:
            if cls._owners.get(port) is owner:
                del cls._owners[port]

    @classmethod
    def is_owned(cls, port: str) -> bool:
        """Return whether the port is currently leased."""
        with cls._lock:
            return port in cls._owners

    @classmethod
    @contextmanager
    def lease(cls, port: str, owner: object) -> Iterator[object]:
        """Acquire a port for a context and release it afterwards."""
        cls.acquire(port, owner)
        try:
            yield owner
        finally:
            cls.release(port, owner)

    @classmethod
    def clear(cls) -> None:
        """Clear all leases; intended for isolated test cleanup."""
        with cls._lock:
            cls._owners.clear()


__all__ = ["PortRegistry"]
