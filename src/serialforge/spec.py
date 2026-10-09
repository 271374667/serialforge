"""Unified immutable declarations for requests and device events."""

from __future__ import annotations

# Approved declaration overloads form one immutable schema; exact type checks
# reject booleans, and the custom constructor replaces legacy inference.
# pylint: disable=too-many-instance-attributes,too-many-arguments,super-init-not-called,unidiomatic-typecheck,too-many-branches,too-many-statements
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar, overload

from serialforge.enums import Correlation, ResponseMode, SpecRole, TimeoutPolicy
from serialforge.errors import CommandError
from serialforge.models import (
    CommandSpec,
    _compiled_pattern,
    _normalise_text,
    _template_fields,
    _validate_result_type,
)

T = TypeVar("T")


@dataclass(frozen=True, eq=False, init=False)
class Spec(CommandSpec, Generic[T]):
    """Declare a response command, explicit NO_REPLY, or keyword-only event.

    Response commands require request and pattern and default to SINGLE.
    Events require only pattern. Count -1, empty strings and total timeout
    -1 disable optional rules. Registered instances are retained by identity.
    """

    role: SpecRole = SpecRole.COMMAND
    pattern: str = ""
    result_type: type[T] = str  # ty: ignore[invalid-assignment]
    name: str = ""
    mode: ResponseMode = ResponseMode.SINGLE
    count: int = -1
    until: str = ""
    error_pattern: str = ""
    timeout_s: float | TimeoutPolicy = field(default=1.0, kw_only=True)
    total_timeout_s: float = -1.0
    parser: Callable[[str], T] | Literal[""] = ""

    @overload
    def __init__(
        self: Spec[str],
        request: str,
        pattern: str,
        *,
        name: str = "",
        mode: ResponseMode = ResponseMode.SINGLE,
        correlation: Correlation = Correlation.EXCLUSIVE,
        count: int = -1,
        until: str = "",
        error_pattern: str = "",
        timeout_s: float | TimeoutPolicy = 1.0,
        idle_timeout_s: float = 0.3,
        total_timeout_s: float = -1.0,
        idempotent: bool = False,
        parser: Callable[[str], str] | Literal[""] = "",
    ) -> None: ...

    @overload
    def __init__(
        self,
        request: str,
        pattern: str,
        result_type: type[T],
        *,
        name: str = "",
        mode: ResponseMode = ResponseMode.SINGLE,
        correlation: Correlation = Correlation.EXCLUSIVE,
        count: int = -1,
        until: str = "",
        error_pattern: str = "",
        timeout_s: float | TimeoutPolicy = 1.0,
        idle_timeout_s: float = 0.3,
        total_timeout_s: float = -1.0,
        idempotent: bool = False,
        parser: Callable[[str], T] | Literal[""] = "",
    ) -> None: ...

    @overload
    def __init__(
        self,
        request: str,
        *,
        mode: Literal[ResponseMode.NO_REPLY],
        name: str = "",
        idempotent: bool = False,
    ) -> None: ...

    @overload
    def __init__(
        self: Spec[str],
        *,
        pattern: str,
        name: str = "",
        parser: Callable[[str], str] | Literal[""] = "",
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        pattern: str,
        result_type: type[T],
        name: str = "",
        parser: Callable[[str], T] | Literal[""] = "",
    ) -> None: ...

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Validate one constructor variant without rebuilding declarations."""
        if len(args) > 3:
            raise CommandError(
                "Spec accepts at most three positional arguments"
            )
        values: dict[str, Any] = {
            "request": "",
            "pattern": "",
            "result_type": str,
            "name": "",
            "mode": ResponseMode.SINGLE,
            "correlation": Correlation.EXCLUSIVE,
            "count": -1,
            "until": "",
            "error_pattern": "",
            "timeout_s": 1.0,
            "idle_timeout_s": 0.3,
            "total_timeout_s": -1.0,
            "idempotent": False,
            "parser": "",
        }
        supplied = dict(
            zip(("request", "pattern", "result_type"), args, strict=False)
        )
        if supplied.keys() & kwargs.keys():
            raise CommandError("duplicate Spec argument")
        supplied.update(kwargs)
        if supplied.keys() - values.keys():
            raise CommandError("unknown Spec argument")
        event = "request" not in supplied
        no_reply = supplied.get("mode") is ResponseMode.NO_REPLY
        if event:
            if supplied.keys() - {"pattern", "result_type", "name", "parser"}:
                raise CommandError("events cannot have command options")
            values["mode"] = ResponseMode.NONE
        elif no_reply:
            if supplied.keys() - {"request", "mode", "name", "idempotent"}:
                raise CommandError("NO_REPLY cannot have response options")
        if not no_reply and "pattern" not in supplied:
            raise CommandError("pattern is required")
        values = {**values, **supplied}
        for key in ("request", "pattern", "name", "until", "error_pattern"):
            if not isinstance(values[key], str):
                raise CommandError(f"{key} must be a string")
        if not event and not _normalise_text(values["request"]):
            raise CommandError("request must be non-empty")
        if not no_reply and not values["pattern"].strip():
            raise CommandError("pattern must be non-empty")
        if not isinstance(values["mode"], ResponseMode) or (
            not event and values["mode"] is ResponseMode.NONE
        ):
            raise CommandError("invalid response mode")
        if not isinstance(values["correlation"], Correlation):
            raise CommandError("invalid correlation")
        count = values["count"]
        if type(count) is not int or (count != -1 and count <= 0):
            raise CommandError("count must be -1 or a positive integer")
        if values["mode"] is ResponseMode.MULTI:
            if (count > 0) == bool(values["until"]):
                raise CommandError(
                    "MULTI requires exactly one of count or until"
                )
        elif count != -1 or values["until"]:
            raise CommandError("count/until require MULTI")
        for key in ("timeout_s", "idle_timeout_s", "total_timeout_s"):
            value = values[key]
            if key == "timeout_s" and value is TimeoutPolicy.AUTO:
                continue
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or (
                    value <= 0
                    and not (key == "total_timeout_s" and value == -1)
                )
            ):
                raise CommandError(f"invalid {key}")
        if type(values["idempotent"]) is not bool:
            raise CommandError("idempotent must be boolean")
        if values["parser"] != "" and not callable(values["parser"]):
            raise CommandError("parser must be callable or empty string")
        if not isinstance(values["result_type"], type):
            raise CommandError("result_type must be a type")
        if not event:
            _template_fields(values["request"], allow_priority=True)
        for key in ("pattern", "until", "error_pattern"):
            if values[key]:
                compiled = _compiled_pattern(values[key], key)
                if key == "pattern" and values["parser"] == "":
                    _validate_result_type(compiled, values["result_type"], key)
        values["name"] = values["name"] or _normalise_text(
            values["pattern"] if event else values["request"]
        )
        if not values["name"].strip():
            raise CommandError("name must be non-empty")
        for key, value in values.items():
            object.__setattr__(self, key, value)
        object.__setattr__(
            self, "role", SpecRole.EVENT if event else SpecRole.COMMAND
        )

    @property
    # ty: ignore[missing-override-decorator] -- Python 3.11 runtime support.
    def placeholders(self) -> tuple[str, ...]:
        """Return wire placeholders without reserving send control names."""
        return _template_fields(self.request, allow_priority=True)


__all__ = ["Spec"]
