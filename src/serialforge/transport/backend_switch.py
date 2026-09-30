"""Process-wide selection of the serial port backend."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from ..protocols import PortBackend
from .serial_transport import SerialTransport


class BackendSwitch:
    """Select a port backend and restore it safely after a test."""

    _backend: PortBackend | None = None

    @classmethod
    def current(cls) -> PortBackend:
        """Return the active backend, creating the real backend lazily."""
        if cls._backend is None:
            cls._backend = SerialTransport()
        return cls._backend

    @classmethod
    @contextmanager
    def use(cls, backend: PortBackend) -> Iterator[PortBackend]:
        """Temporarily replace the active backend."""
        previous = cls._backend
        cls._backend = backend
        try:
            yield backend
        finally:
            cls._backend = previous


__all__ = ["BackendSwitch"]
