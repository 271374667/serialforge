"""Filter backend port descriptors by a device profile's USB identity."""

from __future__ import annotations

from serialforge.models import DeviceInfo, DeviceProfile
from serialforge.protocols import PortBackend
from serialforge.transport import PortRegistry

# The scanner is a small stateless adapter with one public operation.
# pylint: disable=too-few-public-methods


class PortScanner:
    """Enumerate only available ports allowed by one device profile."""

    def __init__(self, profile: DeviceProfile, backend: PortBackend) -> None:
        """Retain a profile and the selected serial backend."""
        self._profile: DeviceProfile = profile
        self._backend: PortBackend = backend

    def scan(self, port: str | None = None) -> list[DeviceInfo]:
        """Return matching descriptors without opening any serial port."""
        matches: list[DeviceInfo] = []
        allowed = set(self._profile.vid_pid)
        for descriptor in self._backend.list_ports():
            name = getattr(descriptor, "device", None)
            vid = getattr(descriptor, "vid", None)
            pid = getattr(descriptor, "pid", None)
            if not isinstance(name, str) or (vid, pid) not in allowed:
                continue
            if port is not None and name.upper() != port.upper():
                continue
            if PortRegistry.is_owned(name):
                continue
            matches.append(
                DeviceInfo(
                    port=name,
                    vid=vid,
                    pid=pid,
                    serial_number=getattr(descriptor, "serial_number", None),
                    location=getattr(descriptor, "location", None),
                    description=getattr(descriptor, "description", None),
                )
            )
        return matches


__all__ = ["PortScanner"]
