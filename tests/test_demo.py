"""Verify that the documented no-hardware demo remains executable."""

from __future__ import annotations

import doctest
import os
import re
import subprocess
import sys
from pathlib import Path

from examples.demo import VERSION, VersionInfo, run_demo
from serialforge import CommandStatus, advanced


def test_demo_runs_through_fake_backend() -> None:
    """Run the example workflow and preserve its declaration identity."""
    result = run_demo()

    assert result.status is CommandStatus.OK
    assert result.data == VersionInfo("1.02")
    assert result.spec is VERSION


def test_readme_python_examples_are_executable() -> None:
    """Execute each README Python block in its own QtCore process."""
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    examples = re.findall(r"```python\n(.*?)\n```", readme, re.DOTALL)
    assert examples
    env = os.environ.copy()
    env.pop("SERIALFORGE_RUN_HARDWARE", None)
    for code in examples:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr


def test_public_configuration_docstring_examples() -> None:
    """Keep the shortest advanced API examples executable."""
    result = doctest.testmod(advanced, raise_on_error=True)
    assert result.attempted > 0
