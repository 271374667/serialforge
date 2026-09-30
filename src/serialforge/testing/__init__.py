"""M1 testing namespace placeholders.

The functional fake backend and simulator are delivered in M2.  Keeping these
names importable now lets API snapshots and downstream test scaffolding land in
the first milestone without touching real serial ports.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass


# A simulator is intentionally a data-only test fixture in M1.
# pylint: disable=too-few-public-methods
@dataclass
class SimulatedDevice:
    """Describe a future scripted simulated device."""

    vid: int
    pid: int
    port: str = "SIM1"


# A transport placeholder gains behavior in M2.
# pylint: disable=too-few-public-methods
class FakeTransport:
    """Placeholder transport used by the M2 fake backend."""


class FakeBackend:
    """Hold simulated devices for future transport tests."""

    devices: tuple[SimulatedDevice, ...]

    def __init__(self, devices: Sequence[SimulatedDevice] = ()) -> None:
        """Create a fake backend with a stable device snapshot."""
        self.devices = tuple(devices)


@contextmanager
def use_fake_backend(backend: FakeBackend) -> Iterator[FakeBackend]:
    """Temporarily select a fake backend; switching is implemented in M2."""
    yield backend


__all__ = [
    "FakeBackend",
    "FakeTransport",
    "SimulatedDevice",
    "use_fake_backend",
]
