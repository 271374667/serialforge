"""Record probe ordering and thread ownership without real serial devices."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence

from serialforge.protocols import TransportProtocol
from tests.support.fake_backend import FakeBackend
from tests.support.fake_transport import FakeTransport
from tests.support.simulated_device import SimulatedDevice


class RecordingBackend(FakeBackend):
    """Track port enumeration, open attempts and overlapping port handles."""

    def __init__(self, devices: Sequence[SimulatedDevice] = ()) -> None:
        """Create a thread-safe observation log for simulated ports."""
        super().__init__(devices)
        self.enumeration_threads: list[int] = []
        self.attempts: list[tuple[str, int, int]] = []
        self.overlapping_ports: list[str] = []
        self._observation_lock: threading.Lock = threading.Lock()
        self._latest: dict[str, FakeTransport] = {}

    # ty: ignore[missing-override-decorator] -- Python 3.11 compatibility.
    def list_ports(self) -> Sequence[SimulatedDevice]:
        """Record the enumeration thread before returning the port list."""
        with self._observation_lock:
            self.enumeration_threads.append(threading.get_ident())
            return super().list_ports()

    # ty: ignore[missing-override-decorator] -- Python 3.11 compatibility.
    def open(
        self, port: str, params: Mapping[str, object]
    ) -> TransportProtocol:
        """Record one rate attempt and detect concurrent handles on a port."""
        with self._observation_lock:
            previous = self._latest.get(port)
            if previous is not None and not previous.closed:
                self.overlapping_ports.append(port)
            rate = params["baudrate"]
            assert isinstance(rate, int)
            self.attempts.append((port, rate, threading.get_ident()))
            transport = super().open(port, params)
            self._latest[port] = self.transports[-1]
            return transport
