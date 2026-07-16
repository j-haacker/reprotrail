#!/usr/bin/env python3
"""Validate the release version across package metadata, code, notes, and tag."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _runtime_version() -> str:
    source = (ROOT / "src/reprotrail/__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if not match:
        raise SystemExit("Could not find reprotrail.__version__")
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Release tag to compare, for example v0.1.0")
    args = parser.parse_args()

    metadata_version = _pyproject_version()
    runtime_version = _runtime_version()
    if runtime_version != metadata_version:
        raise SystemExit(f"Version mismatch: pyproject={metadata_version}, runtime={runtime_version}")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.compile(rf"^## {re.escape(metadata_version)} - \d{{4}}-\d{{2}}-\d{{2}}$", re.MULTILINE)
    if not heading.search(changelog):
        raise SystemExit(f"CHANGELOG.md has no dated {metadata_version} release heading")

    if args.tag and args.tag != f"v{metadata_version}":
        raise SystemExit(f"Tag mismatch: expected v{metadata_version}, got {args.tag}")

    print(f"release metadata is consistent for {metadata_version}")


if __name__ == "__main__":
    main()
