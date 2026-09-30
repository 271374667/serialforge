"""Stable public namespace for no-hardware serial tests."""

from .fake_backend import FakeBackend, use_fake_backend
from .fake_transport import FakeTransport
from .simulated_device import SimulatedDevice

__all__ = [
    "FakeBackend",
    "FakeTransport",
    "SimulatedDevice",
    "use_fake_backend",
]
