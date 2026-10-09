"""Static checks for inferred declarations, handles and unified messages."""

from typing import assert_type

from serialforge import Message, SerialForge, Spec
from serialforge.advanced import CommandCall, TimeoutPolicy


def check(forge: SerialForge) -> None:
    """Check inferred and explicit result types through both send paths."""
    text = Spec("Read", "VALUE:.*")
    integer = Spec("ReadInt", r"(?P<value>\d+)", int)
    assert_type(text, Spec[str])
    assert_type(text.timeout_s, float | TimeoutPolicy)
    assert_type(integer, Spec[int])
    assert_type(forge.add(text), Spec[str])
    assert_type(forge.add("Other", "TEXT"), Spec[str])
    assert_type(forge.add("OtherInt", r"(?P<value>\d+)", int), Spec[int])
    assert_type(forge.send_async(integer), CommandCall[int])
    assert_type(forge.send_sync(text), Message[str])
    assert_type(forge.add(pattern="EVENT"), Spec[str])
