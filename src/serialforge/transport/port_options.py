"""Translate immutable device serial settings into pyserial options."""

from __future__ import annotations

from ..advanced import SerialConfig
from ..enums import FlowControl

# This utility intentionally exposes one static conversion operation.
# pylint: disable=too-few-public-methods


class PortOptions:
    """Translate one serial configuration for either opening path."""

    @staticmethod
    def build(config: SerialConfig, baudrate: int) -> dict[str, object]:
        """Build pyserial options for a known baudrate."""
        return {
            "baudrate": baudrate,
            "bytesize": int(config.data_bits),
            "parity": config.parity.value,
            "stopbits": float(config.stop_bits.value),
            "xonxoff": config.flow_control is FlowControl.XON_XOFF,
            "rtscts": config.flow_control is FlowControl.RTS_CTS,
            "dsrdtr": config.flow_control is FlowControl.DSR_DTR,
            "write_timeout": config.write_timeout_s,
            "timeout": 0.05,
        }


__all__ = ["PortOptions"]
