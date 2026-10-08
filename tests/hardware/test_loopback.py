"""Opt-in TX-RX loopback verification for a real Windows serial port."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Final

import pytest
from PySide6.QtCore import QCoreApplication

from serialforge import (
    CommandResult,
    CommandSpec,
    CommandStatus,
    ConnectionState,
    DeviceInfo,
    DeviceProfile,
    SerialHandler,
)
from serialforge.advanced import FramingConfig, SerialConfig
from serialforge.enums import FramingMode

LOOPBACK_TEXT: Final[str] = "SERIALFORGE_LOOPBACK"
LOOPBACK = CommandSpec(LOOPBACK_TEXT, LOOPBACK_TEXT)


def _setting(name: str) -> str:
    """Read one required hardware setting with a useful error."""
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"set {name} to run the hardware loopback")
    return value


def _wait_for(
    application: QCoreApplication,
    predicate: Callable[[], bool],
    timeout_s: float = 3.0,
) -> None:
    """Pump QtCore events while waiting for a real-port signal."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


@pytest.mark.hardware
def test_real_port_tx_rx_loopback() -> None:
    """Send one line through a physically shorted TX-RX port."""
    port = _setting("SERIALFORGE_HARDWARE_PORT")
    baudrate = int(_setting("SERIALFORGE_HARDWARE_BAUDRATE"))
    application = QCoreApplication.instance() or QCoreApplication([])
    profile = DeviceProfile(
        vid_pid=[(0, 0)],
        baudrates=[baudrate],
        probe=CommandSpec("Identify", r"Device identity .+"),
        serial=SerialConfig(settle_time_s=0),
        framing=FramingConfig(
            mode=FramingMode.LINE,
            terminator=b"\r\n",
        ),
    )
    handler = SerialHandler(profile)
    handler.register(LOOPBACK)
    states: list[ConnectionState] = []
    results: list[CommandResult] = []
    handler.connection_state_changed.connect(states.append)
    handler.command_finished.connect(results.append)
    handler.connect(DeviceInfo(port=port, baudrate=baudrate))
    try:
        _wait_for(
            application,
            lambda: handler.state is ConnectionState.CONNECTED,
        )
        handler.send(LOOPBACK)
        _wait_for(application, lambda: len(results) == 1)
        assert results[0].status is CommandStatus.OK
        assert results[0].spec is LOOPBACK
        assert ConnectionState.CONNECTED in states
    finally:
        handler.disconnect()
