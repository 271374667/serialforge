"""QtCore serial read thread with idle-aware frame delivery."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from serialforge.advanced import FramingConfig
from serialforge.errors import FramingError
from serialforge.protocols import TransportProtocol

# pylint: disable=no-name-in-module
from serialforge.qt_core import QThread
from serialforge.transport import FrameSplitter

# The read loop reports frames to its owning facade through private hooks.
# pylint: disable=protected-access

if TYPE_CHECKING:
    from serialforge.connection.handler_core import HandlerCore


class ReadThread(QThread):
    """Read bytes independently of command writes and idle-frame delivery."""

    def __init__(
        self,
        handler: HandlerCore,
        transport: TransportProtocol,
        config: FramingConfig,
    ) -> None:
        """Bind a transport, frame boundary, and receiver."""
        super().__init__()
        self._handler: HandlerCore = handler
        self._transport: TransportProtocol = transport
        self._splitter: FrameSplitter = FrameSplitter(config)
        self._stop: threading.Event = threading.Event()
        self.failure: Exception | None = None

    def request_stop(self) -> None:
        """Wake a blocked read and request a bounded exit."""
        self._stop.set()
        self._transport.cancel_read()

    # ty: ignore[missing-override-decorator] -- Python 3.11 has no typing.override;
    # do not require a development-only backport to run the installed library.
    def run(self) -> None:
        """Read promptly and flush a silence-gap frame while idle."""
        try:
            while not self._stop.is_set():
                poll = self._handler._dispatcher.stats().poll_interval_s or 0.05
                chunk = self._transport.read(1, min(poll, 0.05))
                if chunk:
                    self._handler.raw_received.emit(chunk)
                if self._stop.is_set():
                    break
                if chunk:
                    parts = [chunk]
                    while True:
                        extra = self._transport.read(4096, 0.0)
                        if not extra:
                            break
                        self._handler.raw_received.emit(extra)
                        parts.append(extra)
                    for frame in self._splitter.feed(b"".join(parts)):
                        self._handler._receive_frame(frame)
                for frame in self._splitter.flush():
                    self._handler._receive_frame(frame)
        except (OSError, RuntimeError, FramingError) as exc:
            if not self._stop.is_set():
                self.failure = exc


__all__ = ["ReadThread"]
