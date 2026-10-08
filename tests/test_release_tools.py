"""Release safety and distribution-boundary regression tests."""

from __future__ import annotations

import io
import subprocess
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
