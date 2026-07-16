#!/usr/bin/env python3
"""Scan publication files for machine-local data and accidental credentials."""

from __future__ import annotations

import fnmatch
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_FILTERS = ROOT / ".privacy-filters.local.toml"
PATTERN_SOURCE_FILES = {
    Path("scripts/check_dist.py"),
    Path("scripts/check_privacy.py"),
}
LOCAL_PATH_PATTERNS = (
    re.compile(r"/(?:home|Users)/[^/\s]+/", re.IGNORECASE),
    re.compile(r"file:///(?:home|Users)/[^/\s]+/", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\", re.IGNORECASE),
)
CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bpypi-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
EDITOR_PARTS = {".idea", ".vscode"}


def _candidate_files() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [Path(raw.decode()) for raw in output.split(b"\0") if raw]


def _local_filters() -> dict[str, tuple[str, ...]]:
    """Load private terms and their allowed path globs from an ignored file."""

    if not LOCAL_FILTERS.exists():
        return {}
    data = tomllib.loads(LOCAL_FILTERS.read_text(encoding="utf-8"))
    raw = data.get("filters", {})
    if not isinstance(raw, dict):
        raise SystemExit(f"{LOCAL_FILTERS.name}: [filters] must be a TOML table")
    filters: dict[str, tuple[str, ...]] = {}
    for term, allowed in raw.items():
        if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
            raise SystemExit(f"{LOCAL_FILTERS.name}: filter {term!r} must map to a list of path globs")
        filters[str(term).casefold()] = tuple(allowed)
    return filters


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _scan_text(relative: Path, text: str, filters: dict[str, tuple[str, ...]]) -> list[str]:
    failures: list[str] = []
    if relative not in PATTERN_SOURCE_FILES:
        for pattern in LOCAL_PATH_PATTERNS:
            if match := pattern.search(text):
                failures.append(f"machine-local path in {relative}: {match.group(0)}")
        for pattern in CREDENTIAL_PATTERNS:
            if match := pattern.search(text):
                failures.append(f"credential-shaped text in {relative}: {match.group(0)[:24]}")

    folded = text.casefold()
    path = relative.as_posix()
    for term, allowed_paths in filters.items():
        if term in folded and not any(fnmatch.fnmatch(path, pattern) for pattern in allowed_paths):
            failures.append(f"local filter matched in {relative}: {term!r}")
    return failures


def main() -> None:
    failures: list[str] = []
    filters = _local_filters()
    for relative in _candidate_files():
        if EDITOR_PARTS.intersection(relative.parts):
            failures.append(f"tracked editor setting: {relative}")
        failures.extend(_scan_text(relative, _read(ROOT / relative), filters))

    generated_sources = ROOT / "docs/_build/html/_sources"
    if generated_sources.exists():
        for path in generated_sources.rglob("*"):
            if path.is_file():
                relative = path.relative_to(ROOT)
                failures.extend(_scan_text(relative, _read(path), filters))

    if failures:
        raise SystemExit("publication privacy scan failed:\n- " + "\n- ".join(failures))
    suffix = f" with {len(filters)} local filters" if filters else ""
    print(f"publication privacy scan passed{suffix}")


if __name__ == "__main__":
    main()
