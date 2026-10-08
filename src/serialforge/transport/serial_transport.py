"""pyserial-backed port backend and byte transport."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import serial
from serial.tools import list_ports

from serialforge.protocols import TransportProtocol


class SerialTransport:
    """Provide the replaceable backend and one opened serial connection."""

    _serial: serial.Serial | None

    def __init__(self, connection: serial.Serial | None = None) -> None:
        """Create a backend or wrap an already opened connection."""
        self._serial = connection

    def list_ports(self) -> Sequence[Any]:
        """Return pyserial port descriptors."""
        return tuple(list_ports.comports())

    def open(
        self, port: str, params: Mapping[str, object]
    ) -> TransportProtocol:
        """Open ``port`` with a mapping of pyserial keyword arguments."""
        values = dict(params)
        values["port"] = port
        return SerialTransport(serial.Serial(**values))

    def read(self, size: int, timeout_s: float) -> bytes:
        """Read up to ``size`` bytes with a bounded timeout."""
        connection = self._require_connection()
        connection.timeout = timeout_s
        return bytes(connection.read(size))

    def write(self, data: bytes) -> int:
        """Write bytes and return the accepted byte count."""
        return cast(int, self._require_connection().write(data))

    def close(self) -> None:
        """Close the connection if it is open."""
        if self._serial is not None:
            self._serial.close()

    def cancel_read(self) -> None:
        """Interrupt a read where the pyserial platform supports it."""
        connection = self._serial
        if connection is not None and hasattr(connection, "cancel_read"):
            connection.cancel_read()

    def cancel_write(self) -> None:
        """Interrupt a write where the pyserial platform supports it."""
        connection = self._serial
        if connection is not None and hasattr(connection, "cancel_write"):
            connection.cancel_write()

    def reset_input_buffer(self) -> None:
        """Discard queued input after an exclusive command timeout."""
        self._require_connection().reset_input_buffer()

    def set_buffer_size(self, rx_size: int) -> None:
        """Request a driver receive buffer size."""
        connection = self._require_connection()
        setter = getattr(connection, "set_buffer_size", None)
        if setter is not None:
            setter(rx_size=rx_size)

    def set_control_lines(
        self, *, dtr: bool | None = None, rts: bool | None = None
    ) -> None:
        """Set DTR/RTS only when the caller explicitly supplied them."""
        connection = self._require_connection()
        if dtr is not None:
            connection.dtr = dtr
        if rts is not None:
            connection.rts = rts

    def _require_connection(self) -> serial.Serial:
        """Return the wrapped connection or fail before touching pyserial."""
        if self._serial is None:
            raise RuntimeError("serial transport is not open")
        return self._serial


__all__ = ["SerialTransport"]
