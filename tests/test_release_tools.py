"""Release safety and distribution-boundary regression tests."""

from __future__ import annotations

import io
import shutil
import subprocess
import tomllib
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts import ci, release


def test_distribution_check_rejects_test_code(tmp_path: Path) -> None:
    """A valid wheel containing a fake backend must fail release validation."""
    wheel = tmp_path / "serialforge-0.0.1-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("serialforge/py.typed", "")
        archive.writestr("serialforge/testing/fake_backend.py", "")
    with pytest.raises(ValueError, match="test content"):
        ci.check_distribution_contents([wheel])


def test_ci_propagates_subprocess_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed validation subprocess must produce a non-zero pipeline exit."""

    def failed_command(
        *_args: object, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["uv"], 17)

    monkeypatch.setattr(ci.subprocess, "run", failed_command)
    with pytest.raises(SystemExit) as error:
        ci.run(["uv", "run", "pytest"])
    assert error.value.code == 17


def test_lowest_keeps_dev_versions_without_constraining_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not resolve Python 2 dev tools or pin away runtime lower bounds."""
    lock = tmp_path / "uv.lock"
    lock.write_text(
        """[[package]]
name = "serialforge"
version = "0.0.1"
[package.dev-dependencies]
dev = [{ name = "pylint" }, { name = "pytest" }]
[[package]]
name = "pylint"
version = "4.1.1"
[[package]]
name = "pytest"
version = "9.1.1"
[[package]]
name = "pyserial"
version = "3.5"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(ci, "ROOT", tmp_path)
    assert set(ci.development_constraints()) == {
        "pylint==4.1.1",
        "pytest==9.1.1",
    }


def test_lowest_edits_only_the_disposable_dev_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the real uv metadata editor without locking or installing tools."""
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "src").mkdir()
    project = root / "pyproject.toml"
    original = """[project]
name = "serialforge"
version = "0.0.1"
requires-python = ">=3.11"
dependencies = ["pyserial>=3.5"]
[dependency-groups]
dev = ["pytest"]
"""
    project.write_text(original, encoding="utf-8")
    checked: list[Path] = []

    def copy_inputs(destination: Path) -> None:
        shutil.copy2(project, destination / "pyproject.toml")

    def constraints() -> list[str]:
        return ["pytest==9.1.1"]

    def validate(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> None:
        if command[1] == "add":
            subprocess.run(
                command,
                cwd=cwd,
                env=env,
                check=True,
                capture_output=True,
                timeout=30,
            )
            assert not (cwd / "uv.lock").exists()
            assert not (cwd / ".venv").exists()
        elif command[1] == "sync":
            metadata = tomllib.loads(
                (cwd / "pyproject.toml").read_text(encoding="utf-8")
            )
            assert metadata["dependency-groups"]["dev"] == ["pytest==9.1.1"]
            assert metadata["project"]["dependencies"] == ["pyserial>=3.5"]
            assert "lowest-direct" in command
            checked.append(cwd)

    monkeypatch.setattr(ci, "ROOT", root)
    monkeypatch.setattr(ci, "TEMP_DIR", tmp_path / "work")
    monkeypatch.setattr(ci, "copy_validation_inputs", copy_inputs)
    monkeypatch.setattr(ci, "development_constraints", constraints)
    monkeypatch.setattr(ci, "run", validate)
    ci.lowest()
    assert len(checked) == 1
    assert project.read_text(encoding="utf-8") == original
    assert not (root / "uv.lock").exists()
    assert not checked[0].exists()


def test_release_dry_run_never_calls_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default release entry point may run checks but cannot upload."""
    commands: list[list[str]] = []

    def validate(_command: list[str]) -> None:
        commands.append(_command)
        (tmp_path / "serialforge.whl").touch()
        (tmp_path / "serialforge.tar.gz").touch()

    def clean() -> None:
        return None

    def unexpected_upload(_artifacts: list[Path], *, target: str) -> None:
        pytest.fail(f"unexpected upload to {target}")

    monkeypatch.setattr(release, "DIST_DIR", tmp_path)
    monkeypatch.setattr(release, "require_clean_worktree", clean)
    monkeypatch.setattr(release, "run", validate)
    monkeypatch.setattr(release, "publish", unexpected_upload)
    monkeypatch.setattr(release.sys, "argv", ["release.py"])
    assert release.main() == 0
    assert commands == [["uv", "run", "python", "scripts/ci.py", "--release"]]


def test_release_rejects_noninteractive_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An automated caller cannot upload even with an explicit flag."""
    monkeypatch.setattr(release.sys, "stdin", io.StringIO("publish testpypi"))
    with pytest.raises(ValueError, match="interactive"):
        release.publish([], target="testpypi")
