"""Default values kept in one place for the public configuration models."""

from __future__ import annotations

from pathlib import Path

LOG_ENABLED: bool = False
LOG_SAVE_TO_FILE: bool = False
LOG_DIR: Path | None = None
LOG_MAX_FILES: int = 50
LOG_MAX_FILE_BYTES: int = 20 * 1024 * 1024

DEFAULT_TERMINATOR: str = "\r\n"
DEFAULT_ENCODING: str = "utf-8"
