"""Filter backend port descriptors by a device profile's USB identity."""

from __future__ import annotations

from typing import Any

from serialforge.models import DeviceInfo, DeviceProfile
from serialforge.protocols import PortBackend
from serialforge.transport import BackendSwitch, PortRegistry

# Descriptor access stays duck-typed at this backend adaptation boundary.


def _device_from_descriptor(descriptor: Any) -> DeviceInfo | None:
    """Map one backend descriptor to an unprobed DeviceInfo record."""
    name = getattr(descriptor, "device", None)
    if not isinstance(name, str):
        return None
    return DeviceInfo(
        port=name,
        vid=getattr(descriptor, "vid", None),
        pid=getattr(descriptor, "pid", None),
        serial_number=getattr(descriptor, "serial_number", None),
        location=getattr(descriptor, "location", None),
        description=getattr(descriptor, "description", None),
    )


def enumerate_ports() -> list[DeviceInfo]:
    """Return every available port as an unprobed DeviceInfo record.

    Descriptors come from the active backend without opening serial
    ports, probing baudrates or filtering by USB identity, so callers
    can list choices before configuring a profile. Ports owned by an
    active connection are excluded because they cannot be opened.
    """
    matches: list[DeviceInfo] = []
    for descriptor in BackendSwitch.current().list_ports():
        device = _device_from_descriptor(descriptor)
        if device is None:
            continue
        if PortRegistry.is_owned(device.port):
            continue
        matches.append(device)
    return matches


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
        allowed = set(self._profile.vid_pid or ())
        for descriptor in self._backend.list_ports():
            name = getattr(descriptor, "device", None)
            vid = getattr(descriptor, "vid", None)
            pid = getattr(descriptor, "pid", None)
            if not isinstance(name, str):
                continue
            if port is None and allowed and (vid, pid) not in allowed:
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


__all__ = ["PortScanner", "enumerate_ports"]
