#!/usr/bin/env python3
"""Scan the publication surface for local details and accidental credentials."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path("scripts/check_privacy.py")
PATTERN_SOURCE_ALLOWED = {SELF, Path("scripts/check_dist.py")}
IDENTITY_ALLOWED = {
    Path("LICENSE"),
    Path("README.md"),
    Path("docs/conf.py"),
    Path("docs/index.md"),
    Path("docs/releasing.md"),
    Path("pyproject.toml"),
    Path("scripts/check_dist.py"),
    Path("tests/test_release.py"),
}
LOCAL_OR_STALE = re.compile(
    r"/home/[^/\s]+|file:///home/|fs71786|boku[-_ ]met|c4v-utils|\bdownscale\b|\bsnippets\b",
    re.IGNORECASE,
)
CREDENTIAL = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bgh[oprsu]_[A-Za-z0-9_]{20,}|\bpypi-[A-Za-z0-9_-]{20,}|"
    r"\bAKIA[0-9A-Z]{16}\b"
)
IDENTITY = re.compile(
    r"Jan Haacker|j-haacker|152862650\+j-haacker@users\.noreply\.github\.com",
    re.IGNORECASE,
)
EDITOR_PARTS = {".idea", ".vscode"}


def _tracked_files() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [Path(raw.decode()) for raw in output.split(b"\0") if raw]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def main() -> None:
    failures: list[str] = []
    for relative in _tracked_files():
        if EDITOR_PARTS.intersection(relative.parts):
            failures.append(f"tracked editor setting: {relative}")
        if relative == SELF:
            continue
        text = _read(ROOT / relative)
        if relative not in PATTERN_SOURCE_ALLOWED and (match := LOCAL_OR_STALE.search(text)):
            failures.append(f"local or obsolete text in {relative}: {match.group(0)}")
        if match := CREDENTIAL.search(text):
            failures.append(f"credential-shaped text in {relative}: {match.group(0)[:24]}")
        if relative not in IDENTITY_ALLOWED and (match := IDENTITY.search(text)):
            failures.append(f"public identity outside allowlist in {relative}: {match.group(0)}")

    generated_sources = ROOT / "docs/_build/html/_sources"
    if generated_sources.exists():
        for path in generated_sources.rglob("*"):
            if path.is_file() and (match := LOCAL_OR_STALE.search(_read(path))):
                failures.append(f"local or obsolete text in generated docs {path}: {match.group(0)}")

    if failures:
        raise SystemExit("publication privacy scan failed:\n- " + "\n- ".join(failures))
    print("publication privacy scan passed")


if __name__ == "__main__":
    main()
