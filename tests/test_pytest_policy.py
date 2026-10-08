"""Verify hardware scheduling without accessing physical serial ports."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_hardware_selection_disables_parallel_workers(tmp_path: Path) -> None:
    """Run a hardware sentinel serially despite parallel defaults."""
    shutil.copy2(
        Path(__file__).with_name("conftest.py"), tmp_path / "conftest.py"
    )
    config = tmp_path / "pytest.ini"
    config.write_text(
        "[pytest]\n"
        "addopts = -q --strict-markers -n 2 --dist worksteal\n"
        "markers = hardware: isolated scheduling sentinel\n",
        encoding="utf-8",
    )
    sentinel = tmp_path / "test_hardware_sentinel.py"
    sentinel.write_text(
        "import pytest\n"
        "from xdist import is_xdist_worker\n"
        "@pytest.mark.hardware\n"
        "def test_serial_controller(request):\n"
        "    assert not is_xdist_worker(request)\n"
        "    assert request.config.getoption('numprocesses') == 0\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["SERIALFORGE_RUN_HARDWARE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(config),
            "--confcutdir",
            str(tmp_path),
            "-m",
            "hardware",
            str(sentinel),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "bringing up nodes" not in result.stdout
