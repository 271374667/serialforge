"""Immutable public data models and declaration-time validation."""

# Public API schemas intentionally keep their complete field sets together.
# pylint: disable=duplicate-code

from __future__ import annotations

import codecs
import re
import string
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from serialforge.enums import (
    CommandStatus,
    Correlation,
    ResponseMode,
    SendRoute,
    TimeoutPolicy,
)
from serialforge.errors import CommandError, ConfigError

if TYPE_CHECKING:
    from serialforge.advanced import FramingConfig, RuntimeConfig, SerialConfig


def _normalise_text(value: str) -> str:
    """Strip whitespace and common line terminators from a declaration."""
    return value.strip().rstrip("\r\n").strip()


def _compiled_pattern(value: str, field_name: str) -> re.Pattern[str]:
    """Compile a regular expression and reject unnamed capture groups."""
    try:
        compiled = re.compile(value)
    except re.error as exc:
        raise CommandError(f"invalid {field_name}: {exc}") from exc
    unnamed = compiled.groups - len(compiled.groupindex)
    if unnamed:
        raise CommandError(
            f"{field_name} contains unnamed capture groups; use (?:...)"
        )
    return compiled


def _template_fields(value: str) -> tuple[str, ...]:
    """Return and validate named placeholders in a command template."""
    result: list[str] = []
    formatter = string.Formatter()
    try:
        parts = formatter.parse(value)
        for _, field_name, format_spec, conversion in parts:
            if field_name is None:
                continue
            if format_spec or conversion:
                raise CommandError(
                    "command placeholders cannot use format specs or "
                    "conversions"
                )
            if not field_name.isidentifier():
                raise CommandError(
                    f"invalid command placeholder: {field_name!r}"
                )
            if field_name == "priority":
                raise CommandError(
                    "command placeholder name 'priority' is reserved"
                )
            result.append(field_name)
    except ValueError as exc:
        raise CommandError(f"invalid request template: {exc}") from exc
    return tuple(result)


def _validate_result_type(
    pattern: re.Pattern[str], result_type: type[Any] | None, field_name: str
) -> None:
    """Check the basic one-to-one relation between groups and result types."""
    if result_type is None:
        return
    if not isinstance(result_type, type):
        raise CommandError(f"{field_name} must be a type")
    if not pattern.groupindex and result_type not in {str, int, float}:
        try:
            result_fields = {item.name for item in fields(result_type)}
        except TypeError as exc:
            raise CommandError(
                f"{field_name} requires a dataclass for named result fields"
            ) from exc
        if result_fields:
            raise CommandError(
                f"{field_name} has no named groups for dataclass fields"
            )
    if pattern.groupindex and result_type not in {str, int, float}:
        try:
            result_fields = {item.name for item in fields(result_type)}
        except TypeError:
            return
        group_fields = set(pattern.groupindex)
        if result_fields != group_fields:
            raise CommandError(
                f"{field_name} fields {sorted(result_fields)} do not match "
                f"pattern groups {sorted(group_fields)}"
            )


# A command declaration is one immutable public schema, not a parameter bag.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True, eq=False)
class CommandSpec:
    """Declare one command request and the response it expects.

    Args:
        request: Text written to the device, optionally containing placeholders.
        pattern: Regular expression used to parse response frames.
        result_type: Optional scalar or dataclass used for parsed data.
        name: Optional display label; defaults to the normalized request.
        mode: Explicit response policy, or inferred SINGLE/NO_REPLY/MULTI.
        correlation: EXCLUSIVE serializes replies; TAGGED uses request fields.
        count: Positive frame count terminating MULTI; excludes until.
        until: Full-match MULTI end marker; excluded from parsed data.
        error_pattern: Device error full-match pattern, producing DEVICE_ERROR.
        timeout_s: Positive first-reply seconds or TimeoutPolicy.AUTO.
        idle_timeout_s: Positive maximum silence between MULTI/STREAM frames.
        total_timeout_s: Optional positive total seconds for the invocation.
        idempotent: Permit configured safe retries after reconnect.
        parser: Optional callable receiving the matched response text.

    Note:
        Frozen with identity equality: register and reuse this exact object.
        See examples/demo.py for an executable declaration and send workflow.

    Raises:
        CommandError: If the declaration is inconsistent or invalid.
    """

    request: str
    pattern: str | None = None
    result_type: type[Any] | None = None
    name: str | None = field(default=None, kw_only=True)
    mode: ResponseMode | None = field(default=None, kw_only=True)
    correlation: Correlation = field(
        default=Correlation.EXCLUSIVE, kw_only=True
    )
    count: int | None = field(default=None, kw_only=True)
    until: str | None = field(default=None, kw_only=True)
    error_pattern: str | None = field(default=None, kw_only=True)
    timeout_s: float | object = field(default=1.0, kw_only=True)
    idle_timeout_s: float = field(default=0.3, kw_only=True)
    total_timeout_s: float | None = field(default=None, kw_only=True)
    idempotent: bool = field(default=False, kw_only=True)
    parser: Callable[[str], Any] | None = field(default=None, kw_only=True)

    # pylint: disable=too-many-branches
    def __post_init__(self) -> None:
        """Validate and infer the response mode."""
        if not isinstance(self.request, str) or not _normalise_text(
            self.request
        ):
            raise CommandError("request must be a non-empty string")
        placeholders = _template_fields(self.request)
        if self.pattern is not None:
            compiled = _compiled_pattern(self.pattern, "pattern")
            _validate_result_type(compiled, self.result_type, "result_type")
        elif self.result_type is not None:
            raise CommandError("result_type requires pattern")
        if self.error_pattern is not None:
            _compiled_pattern(self.error_pattern, "error_pattern")
        if self.until is not None:
            _compiled_pattern(self.until, "until")
        if self.count is not None and self.count <= 0:
            raise CommandError("count must be positive")
        if self.count is not None and self.until is not None:
            raise CommandError("count and until are mutually exclusive")
        if self.idle_timeout_s <= 0:
            raise CommandError("idle_timeout_s must be positive")
        if self.total_timeout_s is not None and self.total_timeout_s <= 0:
            raise CommandError("total_timeout_s must be positive")
        if not isinstance(self.timeout_s, TimeoutPolicy):
            if (
                not isinstance(self.timeout_s, (int, float))
                or self.timeout_s <= 0
            ):
                raise CommandError(
                    "timeout_s must be positive or TimeoutPolicy.AUTO"
                )
        if placeholders and self.mode is ResponseMode.NO_REPLY and self.pattern:
            raise CommandError("NO_REPLY cannot define pattern")
        inferred = self.mode
        if inferred is None:
            if self.count is not None or self.until is not None:
                inferred = ResponseMode.MULTI
            elif self.pattern is None:
                inferred = ResponseMode.NO_REPLY
            else:
                inferred = ResponseMode.SINGLE
        if inferred is ResponseMode.NO_REPLY and (
            self.pattern is not None
            or self.count is not None
            or self.until is not None
        ):
            raise CommandError(
                "NO_REPLY cannot define pattern, count, or until"
            )
        if inferred is ResponseMode.SINGLE and (
            self.count is not None or self.until is not None
        ):
            raise CommandError("SINGLE cannot define count or until")
        if inferred is ResponseMode.STREAM and (
            self.count is not None or self.until is not None
        ):
            raise CommandError("STREAM cannot define count or until")
        if (
            inferred is ResponseMode.MULTI
            and self.pattern is None
            and self.result_type
        ):
            raise CommandError("result_type requires pattern")
        if self.name is None:
            object.__setattr__(self, "name", _normalise_text(self.request))
        elif not self.name.strip():
            raise CommandError("name must not be empty")
        object.__setattr__(self, "mode", inferred)

    @property
    def placeholders(self) -> tuple[str, ...]:
        """Return placeholder names used by the request template."""
        return _template_fields(self.request)


@dataclass(frozen=True, eq=False)
class EventSpec:
    """Declare a device-initiated event while preserving object identity.

    Args:
        pattern: Full-match expression, using named groups for parsed fields.
        result_type: Optional scalar or dataclass for captured data.
        name: Display label; defaults to the pattern, not used for routing.

    Raises:
        CommandError: Pattern or result mapping is invalid.
    """

    pattern: str
    result_type: type[Any] | None = None
    name: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        """Validate the event pattern and result mapping."""
        if not self.pattern.strip():
            raise CommandError("pattern must be a non-empty string")
        compiled = _compiled_pattern(self.pattern, "pattern")
        _validate_result_type(compiled, self.result_type, "result_type")
        if self.name is None:
            object.__setattr__(self, "name", _normalise_text(self.pattern))
        elif not self.name.strip():
            raise CommandError("name must not be empty")


def _default_serial_config() -> Any:
    """Build the advanced default without importing it at module load time."""
    # Import lazily to keep the definition layer free of an import cycle.
    # pylint: disable=import-outside-toplevel
    from serialforge.advanced import SerialConfig

    return SerialConfig()


def _default_runtime_config() -> Any:
    """Build the advanced runtime default without a module cycle."""
    # Import lazily to keep the definition layer free of an import cycle.
    # pylint: disable=import-outside-toplevel
    from serialforge.advanced import RuntimeConfig

    return RuntimeConfig()


# Device identity and transport defaults form one immutable public schema.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True)
class DeviceProfile:
    """Describe how a device is identified, opened and framed.

    Args:
        vid_pid: Non-empty USB identity pairs used for automatic discovery.
        baudrates: Non-empty, unique, positive candidate rates, in probe order.
        probe: SINGLE CommandSpec or advanced.ProbeSpec that cannot match echo.
        name: Optional display name; defaults to the first USB identity.
        terminator: Text suffix for transmitted requests; defaults to CRLF.
        encoding: Text encoding used at the bytes boundary; defaults to UTF-8.
        echo: Drop echoed requests before matching responses when True.
        heartbeat_s: Positive probe interval in seconds; None disables it.
        serial: SerialConfig; None selects 8N1 with normal settling defaults.
        framing: Receive framing, independent of the transmit terminator.
        runtime: Queueing, probing, reconnect and backpressure policy.

    Raises:
        ConfigError: An identity, rate, probe or timing setting is invalid.
    """

    vid_pid: Sequence[tuple[int, int]]
    baudrates: Sequence[int]
    probe: CommandSpec | object
    name: str | None = None
    terminator: str = "\r\n"
    encoding: str = "utf-8"
    echo: bool = False
    heartbeat_s: float | None = None
    serial: SerialConfig | None = None
    framing: FramingConfig | None = None
    runtime: RuntimeConfig | None = None

    # pylint: disable=too-many-branches
    def __post_init__(self) -> None:
        """Validate device identity, probe and default transport settings."""
        pairs = tuple(self.vid_pid)
        if not pairs:
            raise ConfigError("vid_pid must not be empty")
        for pair in pairs:
            if len(pair) != 2 or any(
                isinstance(value, bool) or not 0 <= value <= 0xFFFF
                for value in pair
            ):
                raise ConfigError(
                    "vid_pid values must be integer pairs in 0..65535"
                )
        rates = tuple(self.baudrates)
        if not rates or any(
            isinstance(rate, bool) or rate <= 0 for rate in rates
        ):
            raise ConfigError("baudrates must contain unique positive integers")
        if len(set(rates)) != len(rates):
            raise ConfigError("baudrates must not contain duplicates")
        if (
            not isinstance(self.probe, CommandSpec)
            and type(self.probe).__name__ != "ProbeSpec"
        ):
            raise ConfigError("probe must be CommandSpec or advanced.ProbeSpec")
        if isinstance(self.probe, CommandSpec):
            if self.probe.placeholders:
                raise ConfigError("probe command cannot contain placeholders")
            if (
                self.probe.pattern is None
                or self.probe.mode is not ResponseMode.SINGLE
            ):
                raise ConfigError(
                    "probe command must be a SINGLE command with pattern"
                )
            pattern = re.compile(self.probe.pattern)
            if pattern.fullmatch(_normalise_text(self.probe.request)):
                raise ConfigError("probe pattern must not match its request")
        if not self.terminator:
            raise ConfigError("terminator must not be empty")
        try:
            codecs.lookup(self.encoding)
        except LookupError as exc:
            raise ConfigError(f"unknown encoding: {self.encoding}") from exc
        if self.heartbeat_s is not None and self.heartbeat_s <= 0:
            raise ConfigError("heartbeat_s must be positive")
        if self.serial is None:
            object.__setattr__(self, "serial", _default_serial_config())
        if self.runtime is None:
            object.__setattr__(self, "runtime", _default_runtime_config())
        if self.name is None:
            first_vid, first_pid = pairs[0]
            object.__setattr__(self, "name", f"{first_vid:04X}:{first_pid:04X}")
        object.__setattr__(self, "vid_pid", pairs)
        object.__setattr__(self, "baudrates", rates)


# Logging retention fields belong together in the public configuration object.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True)
class LogConfig:
    """Configure silent-by-default DEBUG logging and connection files.

    Args:
        enabled: Enable traffic output; False is the silent default.
        save_to_file: Additionally write files when enabled is True.
        dir: Optional file directory; None selects the Qt application data path.
        app_name: Optional application name for the default path.
        max_files: Positive number of library-owned files retained (50 default).
        max_file_bytes: Positive traffic byte cap; lifecycle events continue.

    Raises:
        ConfigError: Retention limits or the application name are invalid.
    """

    enabled: bool = False
    save_to_file: bool = False
    dir: Path | None = None
    app_name: str | None = None
    max_files: int = 50
    max_file_bytes: int = 20 * 1024 * 1024

    def __post_init__(self) -> None:
        """Validate log retention limits."""
        if self.max_files <= 0:
            raise ConfigError("max_files must be positive")
        if self.max_file_bytes <= 0:
            raise ConfigError("max_file_bytes must be positive")
        if self.app_name is not None and not self.app_name.strip():
            raise ConfigError("app_name must not be empty")


# Discovery metadata is intentionally represented as one immutable record.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True)
class DeviceInfo:
    """Describe a discovered device and the successful probe.

    Attributes:
        port: OS port name, including COM10 and higher.
        vid: Optional USB vendor ID.
        pid: Optional USB product ID.
        serial_number: Optional stable USB serial number.
        location: Optional USB location, used as a fallback identity.
        description: Optional driver-supplied description.
        baudrate: Working rate, or None for an unprobed descriptor.
        response: Raw successful probe bytes or text, if available.
        elapsed_s: Probe duration in seconds, if measured.
    """

    port: str
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    location: str | None = None
    description: str | None = None
    baudrate: int | None = None
    response: bytes | str | None = None
    elapsed_s: float | None = None


@dataclass(frozen=True)
class DeviceEvent:
    """Carry a device event, stream frame, or unrecognised frame.

    Attributes:
        spec: Original event/STREAM declaration; None for unknown frames.
        request_id: STREAM invocation ID, or None for an unsolicited event.
        data: Parsed value or decoded raw frame text.
        raw_frame: Original receive frame without its delimiter.
        timestamp: Monotonic timestamp in seconds.
    """

    spec: EventSpec | CommandSpec | None = None
    request_id: str | None = None
    data: Any = None
    raw_frame: bytes | str = b""
    timestamp: float = 0.0


# Results carry every value needed by a signal consumer without mutable state.
# pylint: disable=too-many-instance-attributes
@dataclass(frozen=True)
class CommandResult:
    """Carry one invocation's terminal status and parsed response.

    Attributes:
        spec: Original declaration, or None for an unmatched raw text send.
        route: SPEC, MATCHED or RAW resolution path.
        params: Immutable mapping of rendered request parameters.
        sent: Transmitted bytes; empty when no write was attempted.
        status: Terminal CommandStatus; ok is True only for OK.
        data: SINGLE value, MULTI list, or None when no parsed data exists.
        raw_frames: Immutable response frames, retaining original wire contents.
        elapsed_s: Monotonic duration from the write attempt, in seconds.
        request_id: Invocation ID shared with its ticket.
        error_message: Optional explanation of a device, parse or I/O error.
    """

    spec: CommandSpec | None = None
    route: SendRoute = SendRoute.SPEC
    params: Mapping[str, object] = field(default_factory=dict)
    sent: bytes = b""
    status: CommandStatus = CommandStatus.OK
    data: Any = None
    raw_frames: tuple[bytes | str, ...] = ()
    elapsed_s: float = 0.0
    request_id: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        """Freeze mapping and frame containers at the public boundary."""
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
        object.__setattr__(self, "raw_frames", tuple(self.raw_frames))

    @property
    def ok(self) -> bool:
        """Return whether the command completed successfully."""
        return self.status is CommandStatus.OK


__all__ = [
    "DeviceProfile",
    "CommandSpec",
    "EventSpec",
    "LogConfig",
    "CommandResult",
    "DeviceEvent",
    "DeviceInfo",
]
