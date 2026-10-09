"""Validate binding selection in fresh interpreters without GUI imports."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


def run(code: str, api: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run binding isolation checks without mutating the parent environment."""
    env = os.environ.copy()
    if api is not None:
        env.pop("QT_API", None)
        if api:
            env["QT_API"] = api
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_import_does_not_load_gui_or_create_application() -> None:
    """Import only the selected binding's Core module and create no
    app/thread.
    """
    result = run("""
import sys, threading
before = threading.active_count()
import serialforge
from serialforge.qt_core import QCoreApplication
assert QCoreApplication.instance() is None
assert threading.active_count() == before
assert not any(name.endswith(('.QtGui', '.QtWidgets')) for name in sys.modules)
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("api", ["pyqt5", "pyside2", "invalid"])
def test_invalid_binding_selection_raises(api: str) -> None:
    """Reject Qt5/unknown environment selections before initializing
    bindings.
    """
    result = run("import serialforge", api)
    assert result.returncode != 0 and "QT_API" in result.stderr


@pytest.mark.parametrize("loaded", ["PyQt5", "PySide2"])
def test_preloaded_qt5_raises(loaded: str) -> None:
    """Refuse to mix Qt5 with the Qt6-only adapter."""
    result = run(
        "import sys, types; "
        f"sys.modules['{loaded}']=types.ModuleType('{loaded}'); "
        "import serialforge"
    )
    assert result.returncode != 0 and "Qt5 is already loaded" in result.stderr


def test_ambiguous_installed_bindings_require_selection() -> None:
    """Do not import both bindings merely to determine availability."""
    result = run(
        """
import importlib.util
original = importlib.util.find_spec
def available(name, *args, **kwargs):
    if name in ('PySide6', 'PyQt6'):
        return object()
    return original(name, *args, **kwargs)
importlib.util.find_spec = available
import serialforge
""",
        "",
    )
    assert result.returncode != 0 and "set QT_API" in result.stderr


def test_multiple_preloaded_bindings_raise() -> None:
    """Reject multiple loaded runtimes regardless of explicit preference."""
    result = run(
        """
import sys, types
sys.modules['PySide6']=types.ModuleType('PySide6')
sys.modules['PyQt6']=types.ModuleType('PyQt6')
import serialforge
""",
        "pyside6",
    )
    assert result.returncode != 0 and "multiple Qt bindings" in result.stderr


def test_script_core_application_and_main_thread_guard() -> None:
    """Create a retained core app explicitly and reject worker-built facades."""
    result = run("""
import threading
from serialforge import SerialForge
from serialforge.qt_core import QCoreApplication
from serialforge.errors import ConfigError
forge=SerialForge(application='core')
assert QCoreApplication.instance() is not None
errors=[]
def create():
    try: SerialForge()
    except ConfigError as error: errors.append(error)
worker=threading.Thread(target=create)
worker.start(); worker.join()
assert len(errors)==1
forge.disconnect()
""")
    assert result.returncode == 0, result.stderr
