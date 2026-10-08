"""M4 facade contracts before M5 establishes a serial connection."""

import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QTimer

from serialforge import CommandResult, CommandSpec, CommandStatus, DeviceProfile
from serialforge.advanced import CommandTicket
from serialforge.connection import SerialHandler
from serialforge.errors import CommandError

VERSION = CommandSpec("Version", r"Software version (?P<version>\S+)")


def make_handler() -> SerialHandler:
    """Construct a disconnected facade with a valid device profile."""
    profile = DeviceProfile(
        vid_pid=[(0x067B, 0x23A3)],
        baudrates=[115200],
        probe=VERSION,
    )
    return SerialHandler(profile)


def test_register_init_sequence_and_disconnected_send() -> None:
    """Validate declarations synchronously and finish disconnected sends."""
    handler = make_handler()
    with pytest.raises(CommandError, match="尚未注册"):
        handler.send(VERSION)
    handler.register(VERSION)
    handler.set_init_sequence([VERSION, "Version\r\n"])
    with pytest.raises(CommandError, match="not registered"):
        handler.set_init_sequence(["Unknown"])
    results: list[CommandResult] = []
    handler.command_finished.connect(results.append)
    ticket = handler.send("Version")
    assert isinstance(ticket, CommandTicket)
    assert ticket.spec is VERSION
    assert len(results) == 1
    assert results[0].status is CommandStatus.DISCONNECTED
    assert results[0].spec is VERSION
    handler.unregister(VERSION)
    with pytest.raises(CommandError, match="尚未注册"):
        handler.send(VERSION)


def test_send_and_wait_in_script_thread_returns_result() -> None:
    """Return a disconnected result without an event loop or deadlock."""
    handler = make_handler()
    handler.register(VERSION)
    result = handler.send_and_wait(VERSION, timeout=0.1)
    assert result.status is CommandStatus.DISCONNECTED
    assert result.spec is VERSION


def test_send_and_wait_rejects_qt_event_loop_thread() -> None:
    """Avoid blocking a thread that is actively dispatching Qt events."""
    app = QCoreApplication.instance() or QCoreApplication([])
    handler = make_handler()
    handler.register(VERSION)
    errors: list[CommandError] = []

    def attempt() -> None:
        try:
            handler.send_and_wait(VERSION, timeout=0.1)
        except CommandError as exc:
            errors.append(exc)
        finally:
            app.quit()

    QTimer.singleShot(0, attempt)
    QTimer.singleShot(1000, app.quit)
    app.exec()
    assert len(errors) == 1
    assert "event loop" in str(errors[0])


def test_send_and_wait_from_script_thread_receives_worker_result() -> None:
    """A worker can finish the command while the script thread is waiting."""
    handler = make_handler()
    handler.register(VERSION)
    sent = threading.Event()

    def write(data: bytes) -> int:
        sent.set()
        return len(data)

    handler._dispatcher._write = write
    handler._dispatcher.set_connected(True)

    def worker() -> None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not sent.is_set():
            handler._dispatcher.pump()
            time.sleep(0.005)
        if sent.is_set():
            handler._dispatcher.receive_frame(b"Software version 1.02")

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        result = handler.send_and_wait(VERSION, timeout=1)
    finally:
        thread.join(2)
    assert not thread.is_alive()
    assert result.status is CommandStatus.OK
    assert result.spec is VERSION
    assert result.data == {"version": "1.02"}
