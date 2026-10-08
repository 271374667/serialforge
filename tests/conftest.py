"""Shared pytest policy for optional real-hardware tests."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip hardware tests unless the caller explicitly opts in."""
    if (
        os.environ.get("SERIALFORGE_RUN_HARDWARE") == "1"
        and config.getoption("markexpr") == "hardware"
    ):
        return
    skip = pytest.mark.skip(
        reason=(
            "hardware tests require -m hardware, "
            "SERIALFORGE_RUN_HARDWARE=1 and "
            "explicit device settings"
        )
    )
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip)
