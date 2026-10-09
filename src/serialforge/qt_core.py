"""Select exactly one Qt6 Core binding without importing GUI modules.

QT_API may select pyside6 or pyqt6. Otherwise reuse a loaded binding or
require exactly one installed binding. Selection never changes QT_API.
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
    loaded = [name for name in ("PySide6", "PyQt6") if name in sys.modules]
    if any(name in sys.modules for name in ("PySide2", "PyQt5")):
        raise ImportError("serialforge requires Qt6; Qt5 is already loaded")
    requested = os.environ.get("QT_API", "").lower()
    choices = {"pyside6": "PySide6", "pyqt6": "PyQt6"}
    if requested and requested not in choices:
        raise ImportError("QT_API must be pyside6 or pyqt6")
    if len(loaded) > 1:
        raise ImportError("multiple Qt bindings are already loaded")
    if requested:
        selected = choices[requested]
        if loaded and loaded[0] != selected:
            raise ImportError("QT_API conflicts with the loaded Qt binding")
    elif loaded:
        selected = loaded[0]
    else:
        installed = [
            name
            for name in choices.values()
            if importlib.util.find_spec(name) is not None
        ]
        if len(installed) != 1:
            raise ImportError(
                "install serialforge[pyside6] or serialforge[pyqt6]; "
                "set QT_API when both bindings are installed"
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
    else:
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
