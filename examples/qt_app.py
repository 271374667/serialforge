"""Pick the Qt binding for standalone examples.

The shipped examples run under PySide6, PyQt6 and PyQt5 in CI, so they need a
small shim to import the matching ``QCoreApplication``. Real applications should
import ``QApplication`` / ``QCoreApplication`` from their own binding directly:

    from PySide6.QtCore import QCoreApplication

This module exists only so one example file can run under all three bindings.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

_MODULES = {
    "pyside6": "PySide6.QtCore",
    "pyqt6": "PyQt6.QtCore",
    "pyqt5": "PyQt5.QtCore",
}


def _load_core() -> Any:
    """Import the requested binding, or the first one installed."""
    requested = _MODULES.get(os.environ.get("QT_API", "").lower())
    if requested is not None:
        return importlib.import_module(requested)
    for module in _MODULES.values():
        try:
            return importlib.import_module(module)
        except ImportError:
            continue
    raise ImportError("install PySide6, PyQt6 or PyQt5 to run the examples")


QCoreApplication: Any = _load_core().QCoreApplication

__all__ = ["QCoreApplication"]
