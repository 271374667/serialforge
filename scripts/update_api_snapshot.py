"""Regenerate the API snapshot after reviewing a deliberate public API change.

Run with ``uv run python -m scripts.update_api_snapshot`` from the checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.support.api_contract import public_snapshot


def main() -> int:
    """Write a stable JSON contract for review in the Git diff."""
    path = Path(__file__).resolve().parents[1] / "tests" / "api_snapshot.json"
    path.write_text(
        json.dumps(public_snapshot(), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
