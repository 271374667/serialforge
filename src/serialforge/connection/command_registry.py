"""Thread-safe command and event declarations with text request matching."""

from __future__ import annotations

import re
import string
import threading
from collections.abc import Mapping
from types import MappingProxyType

from ..errors import CommandError
from ..models import CommandSpec, EventSpec

RegistrySnapshot = tuple[
    tuple[CommandSpec, ...],
    tuple[EventSpec, ...],
    Mapping[str, CommandSpec],
    tuple[tuple[CommandSpec, re.Pattern[str], int], ...],
]


class CommandRegistry:
    """Publish immutable declaration snapshots for concurrent readers."""

    _lock: threading.RLock
    _snapshot: RegistrySnapshot
    _terminator: str

    def __init__(self, terminator: str = "\r\n") -> None:
        """Create an empty registry without declaring device-specific commands."""
        self._lock = threading.RLock()
        self._terminator = terminator
        self._snapshot = ((), (), MappingProxyType({}), ())

    @property
    def commands(self) -> tuple[CommandSpec, ...]:
        """Return the current immutable command snapshot."""
        return self._snapshot[0]

    @property
    def events(self) -> tuple[EventSpec, ...]:
        """Return the current immutable event snapshot."""
        return self._snapshot[1]

    def contains(self, spec: CommandSpec | EventSpec) -> bool:
        """Check registration by object identity."""
        if isinstance(spec, CommandSpec):
            return spec in self._snapshot[0]
        return spec in self._snapshot[1]

    def register(self, *specs: CommandSpec | EventSpec) -> None:
        """Atomically register declarations, rejecting ambiguous requests.

        Args:
            *specs: Existing immutable declarations to retain by identity.

        Raises:
            CommandError: If a different declaration conflicts with a request.
        """
        with self._lock:
            commands = list(self._snapshot[0])
            events = list(self._snapshot[1])
            for spec in specs:
                if isinstance(spec, CommandSpec):
                    if spec not in commands:
                        commands.append(spec)
                elif isinstance(spec, EventSpec):
                    if spec not in events:
                        events.append(spec)
                else:
                    raise CommandError("register accepts CommandSpec or EventSpec")
            self._publish(commands, events)

    def unregister(self, spec: CommandSpec | EventSpec) -> None:
        """Remove a declaration from future snapshots without changing it."""
        with self._lock:
            if not self.contains(spec):
                return
            commands = [
                item for item in self._snapshot[0] if item is not spec
            ]
            events = [item for item in self._snapshot[1] if item is not spec]
            self._publish(commands, events)

    def _publish(
        self, commands: list[CommandSpec], events: list[EventSpec]
    ) -> None:
        """Validate candidates before replacing the one shared snapshot."""
        exact: dict[str, CommandSpec] = {}
        templates: list[tuple[CommandSpec, re.Pattern[str], int]] = []
        skeletons: set[str] = set()
        for spec in commands:
            request = self.normalise(spec.request, self._terminator)
            skeleton = self.template_skeleton(request)
            if skeleton in skeletons:
                raise CommandError(f"duplicate command request: {request!r}")
            skeletons.add(skeleton)
            if spec.placeholders:
                pattern, score = self.compile_template(request)
                templates.append((spec, pattern, score))
            else:
                exact[request] = spec
        patterns: set[str] = set()
        for spec in events:
            if spec.pattern in patterns:
                raise CommandError(
                    f"duplicate event pattern: {spec.pattern!r}"
                )
            patterns.add(spec.pattern)
        self._snapshot = (
            tuple(commands),
            tuple(events),
            MappingProxyType(exact),
            tuple(templates),
        )

    def resolve_text(
        self, text: str, terminator: str
    ) -> tuple[CommandSpec, dict[str, str]] | None:
        """Match normalised text to an exact request or best template.

        Args:
            text: Caller-supplied request text.
            terminator: Configured line terminator, possibly non-whitespace.

        Returns:
            The original declaration and extracted parameters, if matched.

        Raises:
            CommandError: If input has control characters or templates tie.
        """
        normalised = self.normalise(text, terminator)
        self.validate_text(normalised)
        snapshot = self._snapshot
        exact = snapshot[2].get(normalised)
        if exact is not None:
            return exact, {}
        matches: list[tuple[int, CommandSpec, dict[str, str]]] = []
        for spec, pattern, score in snapshot[3]:
            match = pattern.fullmatch(normalised)
            if match is not None:
                matches.append((score, spec, match.groupdict()))
        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        if len(matches) > 1 and matches[0][0] == matches[1][0]:
            raise CommandError(f"ambiguous command text: {normalised!r}")
        _, spec, params = matches[0]
        return spec, params

    @staticmethod
    def normalise(text: str, terminator: str) -> str:
        """Strip surrounding whitespace and one configured terminator."""
        result = text.strip()
        if terminator and result.endswith(terminator):
            result = result[: -len(terminator)]
        return result.strip()

    @staticmethod
    def validate_text(text: str) -> None:
        """Reject embedded controls that could inject another command."""
        if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise CommandError("command text is empty or contains control characters")

    @staticmethod
    def template_skeleton(request: str) -> str:
        """Replace placeholder names with a common marker for conflicts."""
        pieces: list[str] = []
        for literal, field_name, _, _ in string.Formatter().parse(request):
            pieces.append(literal)
            if field_name is not None:
                pieces.append("{}")
        return "".join(pieces)

    @staticmethod
    def compile_template(request: str) -> tuple[re.Pattern[str], int]:
        """Compile a full-request matcher and count its literal characters."""
        pieces: list[str] = []
        seen: set[str] = set()
        score = 0
        for literal, field_name, _, _ in string.Formatter().parse(request):
            pieces.append(re.escape(literal))
            score += len(literal)
            if field_name is not None:
                if field_name in seen:
                    pieces.append(f"(?P={field_name})")
                else:
                    pieces.append(f"(?P<{field_name}>.+?)")
                    seen.add(field_name)
        return re.compile("".join(pieces)), score


__all__ = ["CommandRegistry"]
