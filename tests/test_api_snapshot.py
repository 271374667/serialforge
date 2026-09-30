"""Public namespace snapshot guard."""

import json
from pathlib import Path

import serialforge
import serialforge.advanced as advanced
import serialforge.errors as errors
import serialforge.testing as testing


def test_public_namespace_snapshot() -> None:
    snapshot = json.loads(
        (Path(__file__).parent / "api_snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    assert serialforge.__all__ == snapshot["serialforge"]
    assert advanced.__all__ == snapshot["advanced"]
    assert errors.__all__ == snapshot["errors"]
    assert testing.__all__ == snapshot["testing"]
