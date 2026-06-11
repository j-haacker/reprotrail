from __future__ import annotations

import json
import subprocess

import pytest

from reprotrail.cli import main
from reprotrail.pixi import PixiGitFreshnessError, check_pixi_git_freshness


def _completed(stdout: str, *, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["pixi"], returncode, stdout=stdout, stderr=stderr)


def _mock_pixi(monkeypatch, payload: dict | None = None, *, returncode: int = 0, stderr: str = ""):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        stdout = json.dumps(payload) if payload is not None else ""
        return _completed(stdout, returncode=returncode, stderr=stderr)

    monkeypatch.setattr("reprotrail.pixi.subprocess.run", fake_run)
    return calls


def _git_change(name: str = "example-library") -> dict:
    return {
        "version": 1,
        "environment": {
            "analysis": {
                "linux-64": [
                    {
                        "name": name,
                        "before": {
                            "pypi": "git+ssh://github/example-org/example-library.git@85d6cc72",
                            "vcs_info": {
                                "vcs": "git",
                                "commit_id": "85d6cc72",
                                "requested_revision": "main",
                            },
                        },
                        "after": {
                            "pypi": "git+ssh://github/example-org/example-library.git@7a69099",
                            "vcs_info": {
                                "vcs": "git",
                                "commit_id": "7a69099",
                                "requested_revision": "main",
                            },
                        },
                        "type": "pypi",
                    }
                ]
            }
        },
    }


def test_check_pixi_git_freshness_fresh_output(tmp_path, monkeypatch):
    calls = _mock_pixi(monkeypatch, {"version": 1, "environment": {}})

    result = check_pixi_git_freshness(
        tmp_path,
        "analysis",
        ("example-library", "reprotrail"),
        manifest_path=tmp_path / "pyproject.toml",
    )

    assert result == {
        "status": "fresh",
        "environment": "analysis",
        "checked_packages": ["example-library", "reprotrail"],
        "packages": [],
    }
    command, kwargs = calls[0]
    assert command == [
        "pixi",
        "update",
        "--dry-run",
        "--json",
        "--manifest-path",
        str(tmp_path / "pyproject.toml"),
        "-e",
        "analysis",
        "example-library",
        "reprotrail",
    ]
    assert kwargs["cwd"] == tmp_path
    assert kwargs["check"] is False


def test_check_pixi_git_freshness_reports_selected_git_change(tmp_path, monkeypatch):
    _mock_pixi(monkeypatch, _git_change())

    result = check_pixi_git_freshness(tmp_path, "analysis", ("example-library",))

    assert result["status"] == "stale"
    assert result["packages"] == [
        {
            "name": "example-library",
            "status": "changed",
            "platform": "linux-64",
            "from": {
                "kind": "git",
                "url": "https://github.com/example-org/example-library",
                "commit": "85d6cc72",
                "requested_revision": "main",
            },
            "to": {
                "kind": "git",
                "url": "https://github.com/example-org/example-library",
                "commit": "7a69099",
                "requested_revision": "main",
            },
        }
    ]


def test_check_pixi_git_freshness_extracts_revision_from_direct_git_url(tmp_path, monkeypatch):
    payload = {
        "version": 1,
        "environment": {
            "analysis": {
                "linux-64": [
                    {
                        "name": "example-library",
                        "before": {"pypi": "git+https://github.com/example-org/example-library@85d6cc72"},
                        "after": {"pypi": "git+https://github.com/example-org/example-library@7a69099"},
                        "type": "pypi",
                    }
                ]
            }
        },
    }
    _mock_pixi(monkeypatch, payload)

    result = check_pixi_git_freshness(tmp_path, "analysis", ("example-library",))

    assert result["status"] == "stale"
    assert result["packages"][0]["from"] == {
        "kind": "git",
        "url": "https://github.com/example-org/example-library",
        "requested_revision": "85d6cc72",
    }
    assert result["packages"][0]["to"] == {
        "kind": "git",
        "url": "https://github.com/example-org/example-library",
        "requested_revision": "7a69099",
    }


def test_check_pixi_git_freshness_ignores_unselected_and_non_git_changes(tmp_path, monkeypatch):
    payload = {
        "version": 1,
        "environment": {
            "analysis": {
                "linux-64": [
                    _git_change("helper-library")["environment"]["analysis"]["linux-64"][0],
                    {
                        "name": "example-library",
                        "before": {"conda": "https://conda.example/ruff-1.0.0.conda", "sha256": "a"},
                        "after": {"conda": "https://conda.example/ruff-1.0.1.conda", "sha256": "b"},
                        "type": "conda",
                    },
                ]
            }
        },
    }
    _mock_pixi(monkeypatch, payload)

    result = check_pixi_git_freshness(tmp_path, "analysis", ("example-library",))

    assert result["status"] == "fresh"
    assert result["packages"] == []


def test_check_pixi_git_freshness_errors_on_pixi_failure(tmp_path, monkeypatch):
    _mock_pixi(monkeypatch, returncode=1, stderr="network unavailable")

    with pytest.raises(PixiGitFreshnessError, match="network unavailable"):
        check_pixi_git_freshness(tmp_path, "analysis", ("example-library",))


def test_check_pixi_git_freshness_errors_on_invalid_json(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        return _completed("not json")

    monkeypatch.setattr("reprotrail.pixi.subprocess.run", fake_run)

    with pytest.raises(PixiGitFreshnessError, match="invalid JSON"):
        check_pixi_git_freshness(tmp_path, "analysis", ("example-library",))


def test_check_pixi_git_freshness_errors_on_os_error(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        raise OSError("pixi missing")

    monkeypatch.setattr("reprotrail.pixi.subprocess.run", fake_run)

    with pytest.raises(PixiGitFreshnessError, match="pixi missing"):
        check_pixi_git_freshness(tmp_path, "analysis", ("example-library",))


def test_check_pixi_git_freshness_errors_on_unparseable_git_change(tmp_path, monkeypatch):
    payload = {
        "version": 1,
        "environment": {
            "analysis": {
                "linux-64": [
                    {
                        "name": "example-library",
                        "before": {"pypi": "git+https://github.com/example-org/example-library.git"},
                        "after": {"pypi": "git+https://github.com/example-org/example-library.git"},
                        "type": "pypi",
                    }
                ]
            }
        },
    }
    _mock_pixi(monkeypatch, payload)

    with pytest.raises(PixiGitFreshnessError, match="could not extract"):
        check_pixi_git_freshness(tmp_path, "analysis", ("example-library",))


def test_cli_pixi_check_git_freshness_json_fresh(monkeypatch, capsys):
    _mock_pixi(monkeypatch, {"version": 1, "environment": {}})

    main(["pixi", "check-git-freshness", "--env", "analysis", "--package", "example-library", "--json"])

    assert json.loads(capsys.readouterr().out) == {
        "status": "fresh",
        "environment": "analysis",
        "checked_packages": ["example-library"],
        "packages": [],
    }


def test_cli_pixi_check_git_freshness_stale_exit(monkeypatch, capsys):
    _mock_pixi(monkeypatch, _git_change())

    with pytest.raises(SystemExit) as exc:
        main(["pixi", "check-git-freshness", "--env", "analysis", "--package", "example-library", "--json"])

    assert exc.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "stale"


def test_cli_pixi_check_git_freshness_json_error(monkeypatch, capsys):
    _mock_pixi(monkeypatch, returncode=1, stderr="network unavailable")

    with pytest.raises(SystemExit) as exc:
        main(["pixi", "check-git-freshness", "--env", "analysis", "--package", "example-library", "--json"])

    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "environment": "analysis",
        "checked_packages": ["example-library"],
        "error": "pixi update dry-run failed: network unavailable",
    }
