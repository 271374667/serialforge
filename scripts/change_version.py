"""Rewrite the package version in every maintained location.

Edit ``VERSION`` below, then run from the checkout root:

    uv run python scripts/change_version.py

Updated automatically: ``pyproject.toml``, ``uv.lock``, the
``PackageNotFoundError`` fallback in ``src/serialforge/__init__.py``, the
version header of ``docs/api.md`` and a new ``CHANGELOG.md`` entry.

Left alone on purpose: literal versions used as test data in ``tests/``,
historical records under ``.agents/`` and the v10 design note at the
repository root.
"""

from __future__ import annotations

import re
import tomllib
from datetime import date
from pathlib import Path

VERSION: str = "1.0.0"

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")


def read(path: Path) -> str:
    """Read UTF-8 text without translating line endings."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def write(path: Path, text: str) -> None:
    """Write UTF-8 text while keeping the file's existing line endings."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def replace_once(path: Path, pattern: str, replacement: str) -> bool:
    """Replace the single match of pattern, failing if the layout changed.

    Args:
        path: File to edit.
        pattern: Regular expression that must match exactly one place.
        replacement: Replacement text, which may reference regex groups.

    Returns:
        True when the file content changed, False when already up to date.

    Raises:
        ValueError: The pattern matched zero or several locations, so the
            file no longer has the shape this script expects.
    """
    text = read(path)
    found = re.findall(pattern, text)
    if len(found) != 1:
        message = (
            f"{path.relative_to(ROOT)}: 期望匹配 1 处版本号，"
            f"实际 {len(found)} 处"
        )
        raise ValueError(message)
    updated = re.sub(pattern, replacement, text)
    if updated == text:
        return False
    write(path, updated)
    return True


def update_pyproject() -> bool:
    """Update the static version read by hatchling."""
    return replace_once(
        ROOT / "pyproject.toml",
        r'(?m)^version = "[^"]*"\r?$',
        f'version = "{VERSION}"',
    )


def update_lock() -> bool:
    """Keep the editable serialforge entry equal to pyproject."""
    return replace_once(
        ROOT / "uv.lock",
        r'(?m)^(\[\[package\]\]\r?\nname = "serialforge"\r?\n)'
        r'version = "[^"]*"\r?$',
        rf'\g<1>version = "{VERSION}"',
    )


def update_fallback_version() -> bool:
    """Update the fallback used when the distribution is not installed."""
    return replace_once(
        ROOT / "src" / "serialforge" / "__init__.py",
        r'(?m)^    __version__ = "[^"]*"\r?$',
        f'    __version__ = "{VERSION}"',
    )


def update_api_doc() -> bool:
    """Update the version header of the API reference."""
    return replace_once(
        ROOT / "docs" / "api.md",
        r"(?m)^> 适用版本: \S+ \|",
        f"> 适用版本: {VERSION} |",
    )


def update_changelog() -> bool:
    """Add a release heading unless one for this version already exists."""
    path = ROOT / "CHANGELOG.md"
    text = read(path)
    heading = f"## {VERSION}"
    if re.search(rf"(?m)^{re.escape(heading)}(?:\s|$)", text):
        return False
    eol = "\r\n" if "\r\n" in text else "\n"
    entry = (
        f"{heading} - {date.today().isoformat()}{eol}"
        f"{eol}- 待填写发布说明。{eol}{eol}"
    )
    first_section = re.search(r"(?m)^## ", text)
    if first_section is None:
        updated = text.rstrip("\r\n") + eol + eol + entry
    else:
        updated = (
            text[: first_section.start()]
            + entry
            + text[first_section.start() :]
        )
    write(path, updated)
    return True


def main() -> int:
    """Validate VERSION and apply it to every maintained location."""
    if VERSION_PATTERN.fullmatch(VERSION) is None:
        raise ValueError(f"VERSION 不是有效的版本号: {VERSION!r}")
    current = tomllib.loads(read(ROOT / "pyproject.toml"))["project"]["version"]
    if current == VERSION:
        print(f"[change_version] 版本已经是 {VERSION}，无需修改")
        return 0
    print(f"[change_version] {current} -> {VERSION}")
    steps = (
        ("pyproject.toml", update_pyproject),
        ("uv.lock", update_lock),
        ("src/serialforge/__init__.py", update_fallback_version),
        ("docs/api.md", update_api_doc),
        ("CHANGELOG.md", update_changelog),
    )
    for name, step in steps:
        changed = step()
        print(f"  {'updated  ' if changed else 'unchanged'}  {name}")
    print("  skipped    tests/、.agents/、根目录方案文档（历史记录与测试夹具）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
