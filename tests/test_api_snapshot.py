"""Public namespace snapshot guard."""

import json
from pathlib import Path

from tests.support.api_contract import public_snapshot


def test_public_namespace_snapshot() -> None:
    snapshot = json.loads(
        (Path(__file__).parent / "api_snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    assert public_snapshot() == snapshot
