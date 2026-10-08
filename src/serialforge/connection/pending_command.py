"""Mutable state for one queued or in-flight command invocation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..advanced import CommandTicket
from ..enums import CommandPriority, SendRoute
from ..models import CommandSpec


@dataclass(eq=False)
class PendingCommand:
    """Keep per-invocation state while retaining the registered spec object."""

    ticket: CommandTicket
    spec: CommandSpec | None
    route: SendRoute
    params: Mapping[str, object]
    sent: bytes
    priority: CommandPriority
    sequence: int
    queued_at: float
    started_at: float | None = None
    first_frame_at: float | None = None
    last_frame_at: float | None = None
    timeout_s: float = 1.0
    raw_frames: list[bytes] = field(default_factory=list)
    values: list[Any] = field(default_factory=list)
    finished: bool = False


__all__ = ["PendingCommand"]
