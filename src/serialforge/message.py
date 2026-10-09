"""Immutable observations shared by signals and invocation results."""

from __future__ import annotations

# One immutable observation keeps invocation and receive metadata together.
# pylint: disable=too-many-instance-attributes
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Generic, TypeVar

from serialforge.enums import CommandStatus, MessageCategory, SendRoute
from serialforge.models import CommandSpec, EventSpec, _freeze_context

T = TypeVar("T")


@dataclass(frozen=True)
class Message(Generic[T]):
    """Carry parsed data, original declaration and per-invocation context.

    RAW_RECEIVE retains exact read chunks in raw_data. Other categories retain
    delimiter-free frames in raw_frames. Context and params are shallow,
    immutable snapshots; context is never transmitted or used for matching.
    """

    category: MessageCategory
    timestamp: float
    data: T | list[T] | None = None
    spec: CommandSpec | EventSpec | None = None
    request_id: str = ""
    raw_frames: tuple[bytes, ...] = ()
    raw_data: bytes = b""
    context: Mapping[str, object] = field(default_factory=dict)
    status: CommandStatus = CommandStatus.NONE
    route: SendRoute = SendRoute.NONE
    params: Mapping[str, object] = field(default_factory=dict)
    sent: bytes = b""
    elapsed_s: float = -1.0
    error_message: str = ""

    def __post_init__(self) -> None:
        """Freeze containers without altering declaration identity."""
        object.__setattr__(self, "context", _freeze_context(self.context))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))
        object.__setattr__(self, "raw_frames", tuple(self.raw_frames))

    @property
    def ok(self) -> bool:
        """Return whether this observation is a successful terminal result."""
        return (
            self.category is MessageCategory.COMMAND_RESULT
            and self.status is CommandStatus.OK
        )

    @property
    def raw_frame(self) -> bytes:
        """Return the sole frame, or empty bytes for zero/multiple frames."""
        return self.raw_frames[0] if len(self.raw_frames) == 1 else b""


__all__ = ["Message"]
