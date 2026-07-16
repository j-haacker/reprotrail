#!/usr/bin/env python3
"""Inspect built distributions for required and accidental content."""

from __future__ import annotations

import argparse
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

REQUIRED_SUFFIXES = {
    "LICENSE",
    "reprotrail/py.typed",
    "reprotrail/templates/product_README.md.template",
}
FORBIDDEN_PARTS = {
    ".git",
    ".github",
    ".pixi",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "docs",
    "tests",
}
FORBIDDEN_TEXT = re.compile(r"/home/[^/\s]+|local-user|file:///home/", re.IGNORECASE)


def _members(path: Path) -> tuple[list[str], list[bytes]]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            payloads = [archive.read(name) for name in names if not name.endswith("/")]
        return names, payloads
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        payloads = []
        for member in archive.getmembers():
            if member.isfile() and (stream := archive.extractfile(member)) is not None:
                payloads.append(stream.read())
        return names, payloads


def _has_suffix(names: list[str], suffix: str) -> bool:
    return any(name == suffix or name.endswith(f"/{suffix}") for name in names)


def check(path: Path) -> None:
    names, payloads = _members(path)
    required = set(REQUIRED_SUFFIXES)
    if path.suffix != ".whl":
        required.add("README.md")
    missing = sorted(suffix for suffix in required if not _has_suffix(names, suffix))
    if missing:
        raise SystemExit(f"{path.name} is missing: {', '.join(missing)}")

    leaked = sorted(name for name in names if FORBIDDEN_PARTS.intersection(PurePosixPath(name).parts))
    if leaked:
        raise SystemExit(f"{path.name} contains forbidden paths: {', '.join(leaked)}")

    for payload in payloads:
        text = payload.decode("utf-8", errors="ignore")
        if match := FORBIDDEN_TEXT.search(text):
            raise SystemExit(f"{path.name} contains local machine text: {match.group(0)}")

    metadata = "\n".join(payload.decode("utf-8", errors="ignore") for payload in payloads)
    expected = (
        "Version: 0.1.0",
        "Requires-Python: >=3.11",
        "Provides-Extra: products",
        "# reprotrail",
        "https://github.com/j-haacker/reprotrail",
        "https://j-haacker.github.io/reprotrail/",
    )
    absent = [value for value in expected if value not in metadata]
    if absent:
        raise SystemExit(f"{path.name} metadata is missing: {', '.join(absent)}")
    print(f"{path.name}: distribution contents passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_dir", nargs="?", default="dist")
    args = parser.parse_args()
    paths = sorted(Path(args.dist_dir).glob("reprotrail-0.1.0*"))
    if len(paths) != 2:
        raise SystemExit(f"Expected one wheel and one sdist in {args.dist_dir}, found {len(paths)}")
    for path in paths:
        check(path)


if __name__ == "__main__":
    main()
