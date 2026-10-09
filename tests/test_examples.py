"""Run the shipped examples and keep the documented code executable."""

from __future__ import annotations

import doctest
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import examples.virtual_device as virtual_device
from examples.virtual_device import VirtualDevice
from serialforge import advanced
from serialforge.enums import SpecRole

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
DOCS = ROOT / "docs"
INCLUDE = re.compile(r'^--8<--\s+"([^"]+)"\s*$', re.MULTILINE)

# 交互示例的输入脚本：覆盖单发单收、单发多收、流式、主动上报、原始文本和退出。
INTERACTIVE_INPUT = (
    "Version\nReadAll\nmonitor\nVersion\nstop\nalarm\nraw Ping\nhelp\nquit\n"
)


def run_example(
    name: str, stdin_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute one example script from the repository root."""
    return subprocess.run(
        [sys.executable, str(EXAMPLES / name)],
        cwd=ROOT,
        input=stdin_text,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


@pytest.mark.parametrize("name", ["quick_start.py", "auto_connect.py"])
def test_examples_run_without_hardware(name: str) -> None:
    """Keep every no-hardware example executable as a standalone script."""
    completed = run_example(name)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip()


def test_interactive_example_answers_every_command() -> None:
    """Drive the console example through one round of every command."""
    completed = run_example("interactive.py", INTERACTIVE_INPUT)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "状态=ok" in completed.stdout
    assert "状态=busy" in completed.stdout
    assert "状态=cancelled" in completed.stdout
    assert "[上报]" in completed.stdout
    assert "[未识别帧]" in completed.stdout


def test_readme_python_examples_are_executable() -> None:
    """Execute each README Python block in its own QtCore process."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    examples = re.findall(r"```python\n(.*?)\n```", readme, re.DOTALL)
    assert examples
    env = os.environ.copy()
    env.pop("SERIALFORGE_RUN_HARDWARE", None)
    for code in examples:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=10,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr


def test_public_configuration_docstring_examples() -> None:
    """Keep the shortest advanced API examples executable."""
    result = doctest.testmod(advanced, raise_on_error=True)
    assert result.attempted > 0


def test_virtual_device_documents_its_declarations() -> None:
    """Fail when a new command is declared without documenting it."""
    table = virtual_device.__doc__ or ""
    for declaration in VirtualDevice().declarations():
        if declaration.role is SpecRole.COMMAND:
            text = declaration.request
        else:
            # 事件没有请求文本，用 pattern 的首词（例如 ALARM）来核对。
            text = declaration.pattern.split(" ", 1)[0]
        assert text in table, declaration


def test_documentation_includes_reference_existing_examples() -> None:
    """Fail when a page includes a missing file, section or end marker."""
    if not DOCS.is_dir():
        pytest.skip("docs/ is not part of the wheel smoke inputs")

    for page in sorted(DOCS.rglob("*.md")):
        for reference in INCLUDE.findall(page.read_text(encoding="utf-8")):
            target, _, section = reference.partition(":")
            snippet = ROOT / target
            assert snippet.is_file(), (page, target)
            if section:
                text = snippet.read_text(encoding="utf-8")
                assert f"--8<-- [start:{section}]" in text, (page, reference)
                assert f"--8<-- [end:{section}]" in text, (page, reference)
