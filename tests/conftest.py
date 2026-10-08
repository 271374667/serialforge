"""Run software tests in parallel and real-hardware tests serially."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_cmdline_main(config: pytest.Config) -> Generator[None, int, int]:
    """Disable xdist before its scheduler starts for shared real ports."""
    if config.getoption("markexpr") == "hardware":
        config.option.numprocesses = 0
    return (yield)


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
