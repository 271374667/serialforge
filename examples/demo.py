"""Run a complete serialforge workflow without a physical serial port.

The example uses the same QtCore signals and asynchronous connection path as a
host application.  ``FakeBackend`` replaces only the transport boundary, so
the command registration and result handling remain representative.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication

from serialforge import (
    CommandResult,
    CommandSpec,
    CommandStatus,
    ConnectionState,
    DeviceProfile,
    SerialHandler,
)
from serialforge.advanced import FramingConfig, RuntimeConfig, SerialConfig
from serialforge.enums import FramingMode
from tests.support import FakeBackend, SimulatedDevice, use_fake_backend


@dataclass(frozen=True)
class VersionInfo:
    """Hold the version returned by the simulated device."""

    version: str


VERSION = CommandSpec(
    "Version",
    r"Software version (?P<version>\d+\.\d+)",
    VersionInfo,
)


def build_profile() -> DeviceProfile:
    """Build the profile used by the simulated device."""
    return DeviceProfile(
        vid_pid=[(0x067B, 0x23A3)],
        baudrates=[115200],
        probe=VERSION,
        serial=SerialConfig(settle_time_s=0),
        framing=FramingConfig(
            mode=FramingMode.SILENCE_GAP,
            silence_gap_s=0.03,
        ),
        runtime=RuntimeConfig(probe_total_timeout_s=2),
    )


def wait_for(
    application: QCoreApplication,
    predicate: Callable[[], bool],
    timeout_s: float = 2.0,
) -> None:
    """Pump Qt events until a zero-argument predicate becomes true."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    if not predicate():
        raise TimeoutError("serialforge demo timed out")


def run_demo() -> CommandResult:
    """Discover a fake device, query its version, and return its result."""
    application = QCoreApplication.instance() or QCoreApplication([])
    backend = FakeBackend(
        [
            SimulatedDevice(
                0x067B,
                0x23A3,
                port="SIM-DEMO",
                responses={
                    b"Version\r\n": b"Software version 1.02",
                },
            )
        ]
    )
    with use_fake_backend(backend):
        handler = SerialHandler(build_profile())
        handler.register(VERSION)
        states: list[ConnectionState] = []
        results: list[CommandResult] = []
        handler.connection_state_changed.connect(states.append)
        handler.command_finished.connect(results.append)
        handler.connect()
        try:
            wait_for(
                application,
                lambda: handler.state is ConnectionState.CONNECTED,
            )
            handler.send(VERSION)
            wait_for(application, lambda: len(results) == 1)
            result = results[0]
            if result.status is not CommandStatus.OK:
                raise RuntimeError(result.error_message or result.status.value)
            if result.spec is not VERSION:
                raise RuntimeError(
                    "demo result did not preserve command identity"
                )
            if ConnectionState.CONNECTED not in states:
                raise RuntimeError("demo never reached CONNECTED")
            return result
        finally:
            handler.disconnect()


def main() -> int:
    """Run the demo and print its parsed result."""
    result = run_demo()
    print(f"status={result.status.value}")
    print(f"data={result.data}")
    print(f"spec_is_VERSION={result.spec is VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
