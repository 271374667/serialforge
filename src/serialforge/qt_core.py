"""Select exactly one Qt Core binding without importing GUI modules.

QT_API may select pyside6, pyqt6 or pyqt5. Otherwise reuse a loaded binding
or require exactly one installed binding. Selection never changes QT_API.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtCore import (
        Property,
        QCoreApplication,
        QObject,
        QStandardPaths,
        Qt,
        QThread,
        QTimer,
        Signal,
        Slot,
    )
else:
    # QT_API name -> top-level module name; order only keeps messages stable.
    _BINDINGS: dict[str, str] = {
        "pyside6": "PySide6",
        "pyqt6": "PyQt6",
        "pyqt5": "PyQt5",
    }
    if "PySide2" in sys.modules:
        raise ImportError(
            "serialforge supports PyQt5, PySide6 and PyQt6, not PySide2"
        )
    loaded = [name for name in _BINDINGS.values() if name in sys.modules]
    requested = os.environ.get("QT_API", "").lower()
    if requested and requested not in _BINDINGS:
        raise ImportError("QT_API must be pyside6, pyqt6 or pyqt5")
    if len(loaded) > 1:
        raise ImportError("multiple Qt bindings are already loaded")
    if requested:
        selected = _BINDINGS[requested]
        if loaded and loaded[0] != selected:
            raise ImportError("QT_API conflicts with the loaded Qt binding")
    elif loaded:
        selected = loaded[0]
    else:
        installed = [
            name
            for name in _BINDINGS.values()
            if importlib.util.find_spec(name) is not None
        ]
        if len(installed) != 1:
            raise ImportError(
                "install serialforge[pyside6], serialforge[pyqt6] or "
                "serialforge[pyqt5]; set QT_API when more than one binding "
                "is installed"
            )
        selected = installed[0]
    if selected == "PySide6":
        from PySide6.QtCore import (
            Property,
            QCoreApplication,
            QObject,
            QStandardPaths,
            Qt,
            QThread,
            QTimer,
            Signal,
            Slot,
        )
    elif selected == "PyQt6":
        from PyQt6.QtCore import (
            QCoreApplication,
            QObject,
            QStandardPaths,
            Qt,
            QThread,
            QTimer,
        )
        from PyQt6.QtCore import (
            pyqtProperty as Property,
        )
        from PyQt6.QtCore import (
            pyqtSignal as Signal,
        )
        from PyQt6.QtCore import (
            pyqtSlot as Slot,
        )
    else:
        from PyQt5.QtCore import (
            QCoreApplication,
            QObject,
            QStandardPaths,
            Qt,
            QThread,
            QTimer,
        )
        from PyQt5.QtCore import (
            pyqtProperty as Property,
        )
        from PyQt5.QtCore import (
            pyqtSignal as Signal,
        )
        from PyQt5.QtCore import (
            pyqtSlot as Slot,
        )

__all__ = [
    "QCoreApplication",
    "QObject",
    "QStandardPaths",
    "Qt",
    "QThread",
    "QTimer",
    "Signal",
    "Slot",
    "Property",
]
