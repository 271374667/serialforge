"""M1 declaration and data-model contracts."""

from collections.abc import MutableMapping
from dataclasses import FrozenInstanceError, dataclass
from typing import cast

import pytest

from serialforge import (
    CommandResult,
    CommandSpec,
    CommandStatus,
    DeviceProfile,
    EventSpec,
)
from serialforge.advanced import DataBits, FlowControl, Parity, SerialConfig
from serialforge.errors import CommandError, ConfigError


@dataclass(frozen=True)
class VersionInfo:
    version: str


def test_command_spec_infers_modes_and_keeps_identity_semantics() -> None:
    no_reply = CommandSpec("Reboot")
    single = CommandSpec("Version", r"Version: (?P<version>\S+)", VersionInfo)
    multi = CommandSpec("Read", r"DATA (?P<value>\d+)", dict, count=2)

    assert no_reply.mode is not None
    assert single.mode is not None
    assert multi.mode is not None
    assert no_reply.mode.value == "no_reply"
    assert single.mode.value == "single"
    assert multi.mode.value == "multi"
    assert no_reply == no_reply
    assert no_reply != CommandSpec("Reboot")
    assert hash(no_reply) != hash(CommandSpec("Reboot"))


def test_command_spec_is_frozen() -> None:
    spec = CommandSpec("Ping")
    with pytest.raises(FrozenInstanceError):
        setattr(spec, "request", "Pong")  # noqa: B010


def test_invalid_command_declarations_fail_fast() -> None:
    with pytest.raises(CommandError, match="count and until"):
        CommandSpec("Read", count=2, until=r"END")
    with pytest.raises(CommandError, match="unnamed capture"):
        CommandSpec("Read", r"DATA (\d+)")
    with pytest.raises(CommandError, match="priority"):
        CommandSpec("Set {priority}")
    with pytest.raises(CommandError, match="result_type"):
        CommandSpec("Raw", result_type=str)


def test_event_spec_is_frozen_and_uses_identity_equality() -> None:
    first = EventSpec(r"EVT (?P<state>UP|DOWN)")
    second = EventSpec(r"EVT (?P<state>UP|DOWN)")
    assert first != second
    with pytest.raises(FrozenInstanceError):
        setattr(first, "name", "changed")  # noqa: B010


def test_profile_has_minimal_required_fields_and_8n1_defaults() -> None:
    probe = CommandSpec("Version", r"Version: (?P<version>\S+)", VersionInfo)
    profile = DeviceProfile([(0x1A86, 0x7523)], [115200], probe)

    assert profile.serial is not None
    assert profile.serial == SerialConfig()
    assert profile.serial.data_bits is DataBits.EIGHT
    assert profile.serial.parity is Parity.NONE
    assert profile.serial.stop_bits.value == "1"
    assert profile.serial.flow_control is FlowControl.NONE
    assert profile.serial.write_timeout_s == 1.0
    assert profile.serial.dtr is None
    assert profile.name == "1A86:7523"


def test_profile_rejects_invalid_probe_and_identity_values() -> None:
    with pytest.raises(ConfigError, match="baudrates"):
        DeviceProfile([(1, 2)], [], CommandSpec("Probe", "OK"))
    with pytest.raises(ConfigError, match="placeholders"):
        DeviceProfile(
            [(1, 2)],
            [9600],
            CommandSpec("Probe {value}", r"OK"),
        )
    with pytest.raises(ConfigError, match="must not match"):
        DeviceProfile([(1, 2)], [9600], CommandSpec("Probe", r"Probe"))


def test_command_result_ok_is_status_derived_and_data_is_frozen() -> None:
    result = CommandResult(
        status=CommandStatus.OK,
        params={"value": "1"},
        raw_frames=(b"OK",),
    )
    assert result.ok is True
    assert result.params["value"] == "1"
    assert result.raw_frames == (b"OK",)
    with pytest.raises(TypeError):
        cast(MutableMapping[str, object], result.params)["other"] = "2"
