"""Static architecture guards required by the design document."""

import ast
import importlib
from pathlib import Path

import serialforge

ROOT = Path(__file__).parents[1]
SRC = Path(serialforge.__file__).parent
TOP_LEVEL_API = {
    "SerialHandler",
    "DeviceFinder",
    "DeviceProfile",
    "CommandSpec",
    "EventSpec",
    "LogConfig",
    "CommandResult",
    "DeviceEvent",
    "DeviceInfo",
    "CommandStatus",
    "ConnectionState",
    "ResponseMode",
    "Correlation",
    "ScanMode",
    "SerialForgeError",
}


def _python_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_non_exempt_modules_have_their_single_entry_class() -> None:
    exempt = {
        "__init__.py",
        "enums.py",
        "models.py",
        "errors.py",
        "settings.py",
        "protocols.py",
        "advanced.py",
    }
    for path in _python_files():
        if path.name in exempt:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        assert len(classes) == 1, path
        expected = "".join(part.capitalize() for part in path.stem.split("_"))
        assert classes[0].name == expected, path


def test_public_exports_match_the_api_budget() -> None:
    serialforge = importlib.import_module("serialforge")
    assert set(serialforge.__all__) == TOP_LEVEL_API
    assert all(not name.startswith("_") for name in serialforge.__all__)
    assert "CommandTicket" not in serialforge.__all__


def test_private_methods_do_not_have_one_public_caller() -> None:
    whitelist = {"run", "__init__", "__post_init__"}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for method_name, method in methods.items():
            if not method_name.startswith("_") or method_name in whitelist:
                continue
            callers = []
            for caller in methods.values():
                if caller is method:
                    continue
                if any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == method_name
                    for node in ast.walk(caller)
                ):
                    callers.append(caller.name)
            assert len(callers) != 1 or callers[0].startswith("_")


def test_logger_calls_only_use_debug() -> None:
    forbidden = {
        "info",
        "warning",
        "error",
        "exception",
        "critical",
        "success",
        "trace",
        "log",
    }
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr in forbidden:
                assert not (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "logger"
                ), path


def test_dependency_layers_and_cross_package_imports_are_one_way() -> None:
    layer = {
        "transport": 1,
        "diagnostics": 1,
        "discovery": 2,
        "connection": 3,
    }
    for path in _python_files():
        package = next((name for name in layer if name in path.parts), None)
        if package is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            target = next(
                (name for name in layer if f".{name}" in node.module), None
            )
            if target is not None:
                assert layer[target] <= layer[package], (path, node.module)


def test_source_uses_absolute_project_imports() -> None:
    """Keep production modules independent of package-relative imports."""
    files = _python_files() + sorted((ROOT / "tests").rglob("*.py"))
    files += sorted((ROOT / "examples").rglob("*.py"))
    files += sorted((ROOT / "scripts").rglob("*.py"))
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, (path, node.module)


def test_fake_backend_is_not_in_the_production_package() -> None:
    """Keep test substitutes out of the wheel's ``serialforge`` package."""
    assert not (SRC / "testing").exists()
    assert (ROOT / "tests" / "support").exists()
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("tests"), path
            if isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith("tests") for alias in node.names
                ), path


def test_forbidden_imports_are_absent() -> None:
    forbidden = {"QtWidgets", "QtGui", "asyncio", "multiprocessing"}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".")[0] not in forbidden
                    for alias in node.names
                )
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden


def test_identity_guard_has_no_copy_or_rebuild_helpers() -> None:
    forbidden = {"copy", "deepcopy", "replace", "asdict"}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden, (path, node.attr)


def test_import_is_side_effect_free() -> None:
    serialforge = importlib.import_module("serialforge")
    assert serialforge.__version__
    assert not (ROOT / "serialforge.log").exists()
