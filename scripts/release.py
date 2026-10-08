"""Validate a release locally; uploading requires an explicit human command."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "outputs" / "01_dist"


def require_clean_worktree() -> None:
    """Fail before building if Git reports modified or untracked files."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if result.stdout.strip():
        raise ValueError("release requires a clean worktree")


def run(command: list[str]) -> None:
    """Execute a command without logging credentials or using a shell."""
    subprocess.run(command, cwd=ROOT, check=True, timeout=3600)


def publish(artifacts: list[Path], *, target: str) -> None:
    """Require typed confirmation before sending artifacts to one registry.

    Args:
        artifacts: The reviewed wheel and sdist from the current CI run.
        target: Either testpypi or pypi; credentials remain in uv's environment.
    """
    if not sys.stdin.isatty():
        raise ValueError("publishing requires an interactive terminal")
    confirmation = f"publish {target}"
    if input(f"Type '{confirmation}' to upload: ").strip() != confirmation:
        raise ValueError("upload cancelled")
    command = ["uv", "publish", *(str(item) for item in artifacts)]
    command += [
        "--keyring-provider",
        "subprocess",
        "--trusted-publishing",
        "never",
    ]
    if target == "testpypi":
        command += ["--publish-url", "https://test.pypi.org/legacy/"]
    run(command)


def main() -> int:
    """Run release CI and optionally publish after human confirmation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument(
        "--target", choices=("testpypi", "pypi"), default="testpypi"
    )
    args = parser.parse_args()
    require_clean_worktree()
    run(["uv", "run", "python", "scripts/ci.py", "--release"])
    artifacts = sorted(DIST_DIR.glob("*.whl")) + sorted(
        DIST_DIR.glob("*.tar.gz")
    )
    if len(artifacts) != 2:
        raise ValueError("expected one fresh wheel and one sdist")
    if args.publish:
        publish(artifacts, target=args.target)
    else:
        print("[release] dry-run passed; no artifacts uploaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
