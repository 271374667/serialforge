"""Parse command responses and device events into declared data shapes."""

from __future__ import annotations

import re
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, get_type_hints

from serialforge.errors import CommandError
from serialforge.models import CommandSpec, EventSpec


# Parsing branches correspond to the public result data-shape table.
# pylint: disable=too-few-public-methods
class ResponseParser:
    """Apply a declaration's pattern and optional result conversion."""

    # Each shape returns its own data type or a contextual conversion error.
    # pylint: disable=too-many-branches,too-many-return-statements
    @staticmethod
    def match(spec: CommandSpec | EventSpec, text: str) -> tuple[bool, Any]:
        """Return whether a frame matches and its parsed data.

        Args:
            spec: Original immutable declaration.
            text: A complete decoded frame without framing bytes.

        Returns:
            A match flag and data, or ``(False, None)``.

        Raises:
            CommandError: If a matched frame cannot be converted.
        """
        if spec.pattern is None:
            if spec.parser is not None:
                try:
                    return True, spec.parser(text)
                except (ValueError, TypeError) as exc:
                    raise CommandError(
                        f"response parser failed: {exc}"
                    ) from exc
            return True, text
        match = re.fullmatch(spec.pattern, text)
        if match is None:
            return False, None
        if isinstance(spec, CommandSpec) and spec.parser is not None:
            try:
                return True, spec.parser(text)
            except (ValueError, TypeError) as exc:
                raise CommandError(f"response parser failed: {exc}") from exc
        result_type = spec.result_type
        captures = match.groupdict()
        if result_type is None:
            return True, captures if captures else match.group(0)
        if result_type in {str, int, float}:
            value = (
                next(iter(captures.values())) if captures else match.group(0)
            )
            try:
                return True, result_type(value)
            except (ValueError, TypeError) as exc:
                raise CommandError(
                    f"response conversion failed: {exc}"
                ) from exc
        if not is_dataclass(result_type):
            raise CommandError("result_type must be a scalar or dataclass")
        hints = get_type_hints(result_type)
        converted: dict[str, Any] = {}
        try:
            for field in fields(result_type):
                raw = captures[field.name]
                field_type = hints[field.name]
                if field_type in {str, int, float}:
                    converted[field.name] = field_type(raw)
                elif isinstance(field_type, type) and issubclass(
                    field_type, Enum
                ):
                    try:
                        converted[field.name] = field_type(raw)
                    except ValueError:
                        converted[field.name] = field_type[raw]
                else:
                    raise CommandError(
                        f"unsupported result field type: {field.name}"
                    )
            return True, result_type(**converted)
        except (KeyError, ValueError, TypeError) as exc:
            raise CommandError(f"response conversion failed: {exc}") from exc


__all__ = ["ResponseParser"]
