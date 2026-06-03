from __future__ import annotations

import pytest

from reprotrail.settings import load_settings


def test_load_settings_rejects_project_wide_license(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.reprotrail.license]
spdx = "MIT"
name = "MIT License"
url = "https://opensource.org/license/mit/"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no longer supported"):
        load_settings(tmp_path)
