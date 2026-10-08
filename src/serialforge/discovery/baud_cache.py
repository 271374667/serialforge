"""Persist successful serial baudrates under a stable device identity."""

from __future__ import annotations

import json
import threading
from os import replace as atomic_replace
from pathlib import Path
from uuid import uuid4

from loguru import logger

# pylint: disable=no-name-in-module
from PySide6.QtCore import QStandardPaths

from ..models import DeviceInfo
from ..settings import LOG_ENABLED


class BaudCache:
    """Keep baudrate hints in memory and optionally in an atomic JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        """Load a cache; ``None`` keeps fake backend scans in memory."""
        self._path: Path | None = path
        self._lock: threading.RLock = threading.RLock()
        self._rates: dict[str, int] = {}
        self._load()

    @classmethod
    def application_cache(cls) -> BaudCache:
        """Locate persistent baud hints in Qt's application data folder."""
        root = Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppLocalDataLocation
            )
        )
        return cls(root / "serialforge" / "baud_cache.json")

    @staticmethod
    def key(device: DeviceInfo) -> str:
        """Prefer USB serial, then physical location, then the COM name."""
        if device.vid is not None and device.pid is not None:
            prefix = f"{device.vid:04X}:{device.pid:04X}"
            if device.serial_number:
                return f"{prefix}:serial:{device.serial_number}"
            if device.location:
                return f"{prefix}:location:{device.location}"
        return f"port:{device.port.upper()}"

    def get(self, device: DeviceInfo) -> int | None:
        """Return a remembered baudrate for this identity, if any."""
        with self._lock:
            return self._rates.get(self.key(device))

    def remember(self, device: DeviceInfo, baudrate: int) -> None:
        """Persist a successful baudrate when it changes."""
        with self._lock:
            key = self.key(device)
            if self._rates.get(key) == baudrate:
                return
            self._rates[key] = baudrate
            self._save()

    def forget(self, device: DeviceInfo) -> None:
        """Remove a stale baudrate hint for a device."""
        with self._lock:
            if self._rates.pop(self.key(device), None) is not None:
                self._save()

    def _load(self) -> None:
        """Ignore a missing or damaged cache without blocking discovery."""
        path = self._path
        if path is None:
            return
        if not path.exists():
            return
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(document, dict):
                return
            self._rates = {
                key: value
                for key, value in document.items()
                if isinstance(key, str)
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value > 0
            }
        except (OSError, ValueError, UnicodeError) as exc:
            if LOG_ENABLED:
                logger.debug("baud cache unavailable: {}", exc)

    def _save(self) -> None:
        """Replace the cache atomically so interrupted writes stay safe."""
        path = self._path
        if path is None:
            return
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(self._rates, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            atomic_replace(temporary, path)
        except OSError as exc:
            if LOG_ENABLED:
                logger.debug("baud cache write failed: {}", exc)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = ["BaudCache"]
