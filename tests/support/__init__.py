"""In-memory serial substitutes used only by tests and local examples."""

from tests.support.fake_backend import FakeBackend, use_fake_backend
from tests.support.fake_transport import FakeTransport
from tests.support.simulated_device import SimulatedDevice

__all__ = [
    "FakeBackend",
    "FakeTransport",
    "SimulatedDevice",
    "use_fake_backend",
]
