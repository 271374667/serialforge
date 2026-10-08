"""Validate source, Python compatibility, dependencies and installed wheels."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "outputs" / "01_dist"
TEMP_DIR = ROOT / "temp" / "0002_m7_ci"
PYTHON_MATRIX = ("3.11", "3.12", "3.13", "3.14")
IDENTITY_CHECK = (
    "from pathlib import Path; from importlib.metadata import distribution; "
    "import serialforge; module = Path(serialforge.__file__).resolve(); "
    "installed = Path(distribution('serialforge').locate_file("
    "'serialforge/__init__.py')).resolve(); "
    "assert module == installed and 'site-packages' in module.parts, module; "
    "print('installed:', serialforge.__version__, serialforge.__file__)"
)


def environment() -> dict[str, str]:
    """Remove inherited Python paths and hardware opt-ins for local CI."""
    result = os.environ.copy()
    for name in (
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "SERIALFORGE_RUN_HARDWARE",
        "UV_NO_SYNC",
    ):
        result.pop(name, None)
    result["QT_API"] = "pyside6"
    return result


def run(
    command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> None:
    """Run one command, propagating failures and bounding hung subprocesses.

    Args:
        command: Argument list passed directly to the subprocess.
        cwd: Directory containing this validation stage's input files.
        env: Optional environment overriding the sanitized CI defaults.
    """
    print("[ci]", " ".join(command), flush=True)
    completed = subprocess.run(
        command, cwd=cwd, env=env or environment(), check=False, timeout=1200
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def source_checks(*, fast: bool) -> None:
    """Run reproducible checks in the current project's locked environment."""
    run(["uv", "sync", "--locked"])
    paths = ["src", "tests", "scripts", "examples"]
    run(["uv", "run", "ruff", "check", *paths])
    run(["uv", "run", "ruff", "format", "--check", *paths])
    run(["uv", "run", "ty", "check"])
    run(["uv", "run", "pytest"])
    if not fast:
        run(["uv", "run", "pylint", "src"])


def check_distribution_contents(artifacts: list[Path]) -> None:
    """Reject test substitutes in both wheel and source distributions."""
    forbidden = {"tests", "testing", "examples", "__pycache__"}
    for artifact in artifacts:
        if artifact.suffix == ".whl":
            with ZipFile(artifact) as archive:
                names = archive.namelist()
        else:
            with tarfile.open(artifact) as archive:
                names = archive.getnames()
        for name in names:
            if forbidden.intersection(Path(name).parts) or name.endswith(
                ".pyc"
            ):
                raise ValueError(f"test content in {artifact.name}: {name}")
        if not any(name.endswith("serialforge/py.typed") for name in names):
            raise ValueError(f"missing py.typed in {artifact.name}")


def build_distributions() -> Path:
    """Build fresh distributions, validate metadata and return the wheel."""
    run(["uv", "build", "--out-dir", str(DIST_DIR), "--clear"])
    artifacts = sorted(DIST_DIR.glob("*.whl")) + sorted(
        DIST_DIR.glob("*.tar.gz")
    )
    if len(artifacts) != 2:
        raise ValueError("expected exactly one wheel and one sdist")
    check_distribution_contents(artifacts)
    run(["uv", "run", "twine", "check", *(str(item) for item in artifacts)])
    return artifacts[0]


def copy_validation_inputs(destination: Path) -> None:
    """Copy the external acceptance suite without the production source tree."""
    for name in ("tests", "examples", "scripts"):
        shutil.copytree(
            ROOT / name,
            destination / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for name in ("pyproject.toml", "README.md", "LICENSE", "CHANGELOG.md"):
        shutil.copy2(ROOT / name, destination / name)


def smoke(wheel: Path, version: str) -> None:
    """Install only the wheel and run external no-hardware acceptance inputs."""
    if not wheel.is_file():
        raise ValueError(f"wheel not found: {wheel}")
    check_distribution_contents([wheel])
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TEMP_DIR, prefix="smoke-") as raw:
        directory = Path(raw)
        copy_validation_inputs(directory)
        env = environment()
        env.pop("UV_PROJECT_ENVIRONMENT", None)
        prefix = [
            "uv",
            "run",
            "--no-project",
            "--isolated",
            "--python",
            version,
            "--with",
            str(wheel.resolve()),
        ]
        run([*prefix, "python", "-c", IDENTITY_CHECK], cwd=directory, env=env)
        run([*prefix, "python", "examples/demo.py"], cwd=directory, env=env)
        run(
            [
                *prefix,
                "--with",
                "pytest",
                "--with",
                "pytest-qt",
                "python",
                "-m",
                "pytest",
                "-c",
                "pyproject.toml",
                "tests",
            ],
            cwd=directory,
            env=env,
        )


def matrix() -> None:
    """Run the suite with an independent environment for each Python version."""
    for version in PYTHON_MATRIX:
        env = environment()
        env["UV_PROJECT_ENVIRONMENT"] = str(
            ROOT / f".venv-py{version.replace('.', '')}"
        )
        run(["uv", "sync", "--locked", "--python", version], env=env)
        run(["uv", "run", "--python", version, "--no-sync", "pytest"], env=env)


def development_constraints() -> list[str]:
    """Keep locked development tools while lowering runtime dependencies."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    project = next(
        package
        for package in lock["package"]
        if package["name"] == "serialforge"
    )
    names = {
        dependency["name"] for dependency in project["dev-dependencies"]["dev"]
    }
    constraints: list[str] = []
    for package in lock["package"]:
        if package["name"] not in names:
            continue
        requirement = f"{package['name']}=={package['version']}"
        markers = package.get("resolution-markers", [])
        if markers:
            requirement += "; " + " or ".join(
                f"({marker})" for marker in markers
            )
        constraints.append(requirement)
    return constraints


def lowest() -> None:
    """Validate the lowest runtime dependencies with locked dev tools."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=TEMP_DIR, prefix="lowest-") as raw:
        directory = Path(raw)
        copy_validation_inputs(directory)
        shutil.copytree(
            ROOT / "src",
            directory / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        env = environment()
        env["UV_PROJECT_ENVIRONMENT"] = str(directory / ".venv")
        run(
            [
                "uv",
                "add",
                "--dev",
                "--frozen",
                *development_constraints(),
            ],
            cwd=directory,
            env=env,
        )
        run(
            ["uv", "sync", "--python", "3.11", "--resolution", "lowest-direct"],
            cwd=directory,
            env=env,
        )
        run(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                "-c",
                "from importlib.metadata import version; "
                "print({n: version(n) for n in "
                "['PySide6-Essentials', 'pyserial', 'loguru']})",
            ],
            cwd=directory,
            env=env,
        )
        run(["uv", "run", "--no-sync", "pytest"], cwd=directory, env=env)


def parse_args() -> argparse.Namespace:
    """Select a CI mode and optional existing wheel for standalone smoke."""
    parser = argparse.ArgumentParser(description="serialforge local CI")
    modes = parser.add_mutually_exclusive_group()
    for mode in ("fast", "matrix", "lowest", "smoke", "release"):
        modes.add_argument(f"--{mode}", action="store_true")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--python", default="3.11", help="smoke interpreter")
    return parser.parse_args()


def main() -> int:
    """Execute selected stages while protecting the repository lock file."""
    args = parse_args()
    lock = ROOT / "uv.lock"
    original = hashlib.sha256(lock.read_bytes()).digest()
    try:
        if args.smoke:
            wheel = args.wheel or next(
                iter(sorted(DIST_DIR.glob("*.whl"))), None
            )
            if wheel is None:
                raise ValueError("--smoke requires a built wheel or --wheel")
            smoke(wheel.resolve(), args.python)
        else:
            source_checks(fast=args.fast)
            if not args.fast:
                wheel = build_distributions()
                smoke(wheel, args.python)
                if not args.lowest:
                    matrix()
                if args.lowest or args.release:
                    lowest()
    finally:
        if hashlib.sha256(lock.read_bytes()).digest() != original:
            raise RuntimeError("CI unexpectedly modified uv.lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
