"""Validate binding selection in fresh interpreters without GUI imports."""

from __future__ import annotations

import importlib.util
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


@pytest.mark.parametrize(
    ("api", "module"),
    [
        ("pyside6", "PySide6.QtCore"),
        ("pyqt6", "PyQt6.QtCore"),
        ("pyqt5", "PyQt5.QtCore"),
    ],
)
def test_serial_forge_reuses_host_application(api: str, module: str) -> None:
    """Accept a QApplication/QCoreApplication created by the host binding."""
    if importlib.util.find_spec(module.split(".", 1)[0]) is None:
        pytest.skip(f"{module} is not installed")
    result = run(
        f"""
import importlib
from serialforge import SerialForge

core = importlib.import_module({module!r})
app = core.QCoreApplication([])
forge = SerialForge()
print(forge.state.value)
forge.disconnect()
""",
        api,
    )
    assert result.returncode == 0, result.stderr
    assert "disconnected" in result.stdout


def test_serial_forge_requires_existing_application() -> None:
    """Fail clearly when the host has not created its Qt application yet."""
    result = run("""
from serialforge import SerialForge
from serialforge.errors import ConfigError

try:
    SerialForge()
except ConfigError as error:
    text = str(error)
    assert "QApplication" in text and "QCoreApplication" in text
else:
    raise AssertionError("SerialForge must require an existing Qt application")
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("api", ["pyside2", "invalid"])
def test_invalid_binding_selection_raises(api: str) -> None:
    """Reject unsupported or unknown selections before initializing
    bindings.
    """
    result = run("import serialforge", api)
    assert result.returncode != 0 and "QT_API" in result.stderr


def test_preloaded_pyside2_raises() -> None:
    """Refuse to mix the unsupported PySide2 runtime with the adapter."""
    result = run(
        "import sys, types; "
        "sys.modules['PySide2']=types.ModuleType('PySide2'); "
        "import serialforge"
    )
    assert result.returncode != 0 and "PySide2" in result.stderr


def test_ambiguous_installed_bindings_require_selection() -> None:
    """Do not import both bindings merely to determine availability."""
    result = run(
        """
import importlib.util
original = importlib.util.find_spec
def available(name, *args, **kwargs):
    if name in ('PySide6', 'PyQt6', 'PyQt5'):
        return object()
    return original(name, *args, **kwargs)
importlib.util.find_spec = available
import serialforge
""",
        "",
    )
    assert result.returncode != 0 and "set QT_API" in result.stderr


def test_conflicting_loaded_binding_raises() -> None:
    """Reject an explicit preference that disagrees with a loaded binding."""
    result = run(
        "import sys, types; "
        "sys.modules['PyQt5']=types.ModuleType('PyQt5'); "
        "import serialforge",
        "pyside6",
    )
    assert result.returncode != 0 and "conflicts" in result.stderr


@pytest.mark.parametrize(
    "loaded",
    [
        ("PySide6", "PyQt6"),
        ("PySide6", "PyQt5"),
        ("PyQt6", "PyQt5"),
    ],
)
def test_multiple_preloaded_bindings_raise(loaded: tuple[str, str]) -> None:
    """Reject multiple loaded runtimes regardless of explicit preference."""
    code = (
        "import sys, types\n"
        + "".join(
            f"sys.modules['{name}']=types.ModuleType('{name}')\n"
            for name in loaded
        )
        + "import serialforge\n"
    )
    result = run(code, "pyside6")
    assert result.returncode != 0 and "multiple Qt bindings" in result.stderr


@pytest.mark.parametrize(
    ("api", "module"),
    [("pyside6", "PySide6"), ("pyqt6", "PyQt6"), ("pyqt5", "PyQt5")],
)
def test_selected_binding_imports_when_installed(api: str, module: str) -> None:
    """Import QtCore through each binding that is installed in this env."""
    if importlib.util.find_spec(module) is None:
        pytest.skip(f"{module} is not installed")
    result = run(
        "from serialforge.qt_core import QCoreApplication, Qt, Signal; "
        "print('ok', QCoreApplication.__module__.split('.')[0])",
        api,
    )
    assert result.returncode == 0, result.stderr
    assert f"ok {module}" in result.stdout


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
