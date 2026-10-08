"""Inspect the Python-owned public API for an explicit release snapshot."""

from __future__ import annotations

import ast
import inspect
from dataclasses import fields, is_dataclass
from enum import Enum
from types import ModuleType
from typing import Any

import serialforge
import serialforge.advanced as advanced
import serialforge.errors as errors


def class_contract(cls: type[Any]) -> dict[str, Any]:
    """Describe public constructors, methods, properties, signals and fields.

    Qt's inherited C++ members are outside this library's API budget. Signal
    declarations are read from source to avoid binding-version-dependent reprs.
    """
    result: dict[str, Any] = {}
    if issubclass(cls, Enum):
        result["members"] = {
            name: item.value for name, item in cls.__members__.items()
        }
        return result
    methods: dict[str, str] = {}
    properties: dict[str, str] = {}
    for name, value in vars(cls).items():
        if name.startswith("_") and name != "__init__":
            continue
        if inspect.isfunction(value):
            methods[name] = str(inspect.signature(value))
        elif isinstance(value, property) and value.fget is not None:
            properties[name] = str(inspect.signature(value.fget))
    result["methods"] = methods
    result["properties"] = properties
    tree = ast.parse(inspect.getsource(cls))
    class_node = tree.body[0]
    assert isinstance(class_node, ast.ClassDef)
    signals: dict[str, str] = {}
    for node in class_node.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "Signal"
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    signals[target.id] = ast.unparse(node.value)
    result["signals"] = signals
    if is_dataclass(cls):
        result["fields"] = [
            {"name": item.name, "type": str(item.type), "kw_only": item.kw_only}
            for item in fields(cls)
        ]
    else:
        result["attributes"] = (
            {
                name: str(value)
                for name, value in cls.__annotations__.items()
                if not name.startswith("_")
            }
            if "__annotations__" in vars(cls)
            else {}
        )
    return result


def public_snapshot() -> dict[str, Any]:
    """Collect namespaces and every class owned by serialforge."""
    result: dict[str, Any] = {}
    classes: dict[str, Any] = {}
    modules: tuple[ModuleType, ...] = (serialforge, advanced, errors)
    for module in modules:
        key = module.__name__.removeprefix("serialforge.")
        result[key] = module.__all__
        for name in module.__all__:
            value = vars(module)[name]
            if inspect.isclass(value):
                classes[f"{module.__name__}.{name}"] = class_contract(value)
    result["classes"] = classes
    return result
