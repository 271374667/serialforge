"""Transport-layer implementations and test injection points."""

from serialforge.transport.backend_switch import BackendSwitch
from serialforge.transport.checksum_calculator import ChecksumCalculator
from serialforge.transport.frame_splitter import FrameSplitter
from serialforge.transport.latency_tracker import LatencyTracker
from serialforge.transport.port_options import PortOptions
from serialforge.transport.port_registry import PortRegistry
from serialforge.transport.serial_transport import SerialTransport

__all__ = [
    "BackendSwitch",
    "ChecksumCalculator",
    "FrameSplitter",
    "LatencyTracker",
    "PortRegistry",
    "PortOptions",
    "SerialTransport",
]
