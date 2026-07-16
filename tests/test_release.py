from __future__ import annotations

import re
import tomllib
from pathlib import Path

import reprotrail

ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_is_consistent():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]

    assert reprotrail.__version__ == version
    assert pyproject["project"]["license-files"] == ["LICENSE"]
    assert pyproject["project"]["urls"] == {
        "Documentation": "https://j-haacker.github.io/reprotrail/",
        "Changelog": "https://github.com/j-haacker/reprotrail/blob/main/CHANGELOG.md",
        "Issues": "https://github.com/j-haacker/reprotrail/issues",
        "Source": "https://github.com/j-haacker/reprotrail",
    }
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## {re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.MULTILINE)
