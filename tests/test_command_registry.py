"""Contracts for runtime declarations and text command resolution."""

import re
from dataclasses import dataclass

import pytest

from serialforge import CommandSpec, EventSpec
from serialforge.connection.command_registry import CommandRegistry
from serialforge.errors import CommandError


@dataclass(frozen=True)
class VersionInfo:
    """Represent the varying version number in the real device reply."""

    version: str


def test_identity_duplicate_and_atomic_rejection() -> None:
    """Keep original declarations and leave the snapshot intact on error."""
    registry = CommandRegistry()
    version = CommandSpec(
        "Version",
        r"Software version (?P<version>\d+\.\d+)",
        VersionInfo,
    )
    event = EventSpec(r"EVT (?P<state>ON|OFF)")
    registry.register(version, event, version)
    before = registry.commands
    assert registry.commands == (version,)
    assert registry.events == (event,)
    assert registry.resolve_text("Version\r\n", "\r\n") == (version, {})
    for reply in ("Software version 1.02", "Software version 1.75"):
        assert re.fullmatch(version.pattern or "", reply)
    with pytest.raises(CommandError, match="duplicate command"):
        registry.register(CommandSpec("  Version\n"))
    assert registry.commands is before
    assert registry.commands[0] is version


def test_templates_choose_more_literal_text_and_extract_params() -> None:
    """Exact text wins, then the template with more literal characters."""
    registry = CommandRegistry()
    general = CommandSpec("Set {setting}")
    specific = CommandSpec("SetMode {mode}")
    exact = CommandSpec("SetMode run")
    registry.register(general, specific, exact)
    assert registry.resolve_text("SetMode run", "\r\n") == (exact, {})
    assert registry.resolve_text("SetMode stop", "\r\n") == (
        specific,
        {"mode": "stop"},
    )
    assert registry.resolve_text("Set speed", "\r\n") == (
        general,
        {"setting": "speed"},
    )
    assert registry.resolve_text("setmode stop", "\r\n") is None


def test_custom_terminator_conflict_and_unregister() -> None:
    """Configured non-whitespace terminators are normalised consistently."""
    registry = CommandRegistry(";")
    command = CommandSpec("Version")
    registry.register(command)
    assert registry.resolve_text(" Version ; \r\n", ";") == (
        command,
        {},
    )
    with pytest.raises(CommandError, match="duplicate command"):
        registry.register(CommandSpec("Version;"))
    registry.unregister(command)
    assert registry.resolve_text("Version;", ";") is None


def test_reject_ambiguous_templates_and_embedded_control_text() -> None:
    """A tie or command injection is rejected before any transport write."""
    registry = CommandRegistry()
    registry.register(CommandSpec("A{x}C{z}"), CommandSpec("A{y}D{z}"))
    with pytest.raises(CommandError, match="ambiguous"):
        registry.resolve_text("ABCDQ", "\r\n")
    with pytest.raises(CommandError, match="control"):
        registry.resolve_text("Version\r\nReboot", "\r\n")
