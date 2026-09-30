"""In-memory port backend for tests and examples."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager

from ..protocols import TransportProtocol
from ..transport import BackendSwitch, PortRegistry
from .fake_transport import FakeTransport
from .simulated_device import SimulatedDevice


class FakeBackend:
    """Expose simulated devices through the port backend protocol."""

    def __init__(self, devices: Sequence[SimulatedDevice] = ()) -> None:
        """Create a backend with a stable device snapshot."""
        self.devices = tuple(devices)
        self.transports: list[FakeTransport] = []

    def list_ports(self) -> Sequence[SimulatedDevice]:
        """Return descriptors matching pyserial's common attributes."""
        return tuple(
            item
            for item in self.devices
            if not PortRegistry.is_owned(item.port)
        )

    def open(
        self, port: str, params: Mapping[str, object]
    ) -> TransportProtocol:
        """Open a simulated port and record its transport for assertions."""
        del params
        device = next(
            (item for item in self.devices if item.port == port), None
        )
        if device is None:
            raise OSError(f"simulated port not found: {port}")
        transport = FakeTransport(device)
        self.transports.append(transport)
        return transport


@contextmanager
def use_fake_backend(backend: FakeBackend) -> Iterator[FakeBackend]:
    """Temporarily make ``backend`` the process-wide active backend."""
    with BackendSwitch.use(backend):
        yield backend


__all__ = ["FakeBackend", "use_fake_backend"]
