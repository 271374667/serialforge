"""Run serialforge's local validation pipeline.

The script intentionally invokes uv commands as argument lists so it works on
PowerShell without relying on shell wildcard expansion.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    """Run one command and stop on failure."""
    print("[ci]", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> int:
    """Parse CI options and execute the selected checks."""
    parser = argparse.ArgumentParser(description="serialforge local CI")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--lowest", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()

    if args.lowest:
        run(["uv", "sync", "--resolution", "lowest-direct"])
    else:
        run(["uv", "sync", "--locked"])
    run(["uv", "run", "ruff", "format", "--check", "src", "tests", "scripts"])
    run(["uv", "run", "ty", "check"])
    run(["uv", "run", "pytest"])
    if args.fast:
        return 0
    run(["uv", "run", "pylint", "src"])
    run(["uv", "build"])
    wheels = sorted(
        item
        for item in (
            (ROOT / "dist").glob("*") if (ROOT / "dist").exists() else []
        )
        if item.suffix in {".whl", ".gz"}
    )
    if wheels:
        run(["uv", "run", "twine", "check", *(str(item) for item in wheels)])
    if args.matrix:
        print(
            "[ci] Python matrix requested; run uv run --python for each "
            "3.11-3.14"
        )
    if args.smoke:
        run(["uv", "run", "python", "-c", "import serialforge"])
    if args.release:
        print("[ci] release mode completed; publishing is deliberately omitted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
