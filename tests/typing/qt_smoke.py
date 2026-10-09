"""Small ty fixture for the QtCore and object-identity contracts."""

from __future__ import annotations

from typing_extensions import override

from serialforge import CommandResult, CommandSpec
from serialforge.qt_core import QThread, Signal, Slot


class SmokeThread(QThread):
    """Emit one Python object from a QThread callback."""

    result_ready = Signal(object)

    @override
    def run(self) -> None:
        """Emit a result without starting a second event loop."""
        spec = CommandSpec("Ping")
        self.result_ready.emit(CommandResult(spec=spec))


class SmokeReceiver:
    """Receive Qt signals while preserving object identity."""

    received: CommandResult | None

    def __init__(self) -> None:
        self.received = None

    @Slot(object)
    def receive(self, value: object) -> None:
        """Accept a signal payload and inspect its command identity."""
        if isinstance(value, CommandResult):
            self.received = value
            spec = value.spec
            if spec is not None:
                _same_object(value, spec)


def _same_object(result: CommandResult, spec: CommandSpec) -> bool:
    """Exercise the ``is`` contract used by application code."""
    return result.spec is spec


def wire_smoke() -> None:
    """Exercise connect and emit descriptors for ty."""
    thread = SmokeThread()
    receiver = SmokeReceiver()
    thread.result_ready.connect(receiver.receive)
    thread.result_ready.emit(CommandResult())
