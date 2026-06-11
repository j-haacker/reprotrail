from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from reprotrail import epochs
from reprotrail.pixi import (
    editable_dependency_failures,
    environment_summary,
    package_records,
    pixi_dependency_records,
    repo_paths_with_dependencies,
)


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _git_repo(path):
    path.mkdir()
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "test@example.invalid"], path)
    _run(["git", "config", "user.name", "Test User"], path)
    (path / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _run(["git", "add", "tracked.txt"], path)
    _run(["git", "commit", "-m", "initial"], path)
    return path


def _snapshot(seed: str, *, lock: str = "lock-a") -> dict:
    payload = {
        "schema_version": "1",
        "kind": "reprotrail-dependency-snapshot",
        "pixi": {
            "environment": "dev",
            "lockfile": {"path": "pixi.lock", "sha256": lock},
            "local_path_dependencies": ["../dep"],
        },
        "packages": {"reprotrail": "0.1.0"},
        "editable_dependencies": [
            {
                "package": "dep",
                "repo": "dep",
                "path": "../dep",
                "git": {"commit": seed, "dirty": False},
            }
        ],
        "platform": {"system": "Linux", "machine": "x86_64"},
    }
    payload["digest"] = epochs.stable_digest(payload)
    return payload


def test_pixi_dependency_records_classify_self_and_external_paths(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    lock_text = (
        "version: 6\n"
        "environments:\n"
        "  dev:\n"
        "    packages:\n"
        "      linux-64:\n"
        "      - pypi: ./\n"
        "      - pypi: ../dep\n"
        "packages:\n"
        "- pypi: ./\n"
        "  name: project\n"
        "- pypi: ../dep\n"
        "  name: dep\n"
    )

    records = pixi_dependency_records(lock_text, "dev", project)

    by_path = {record["path"]: record for record in records}
    assert by_path["./"]["kind"] == "project-self"
    assert by_path["./"]["package"] == "project"
    assert by_path["../dep"]["kind"] == "external-editable"
    assert by_path["../dep"]["package"] == "dep"
    assert editable_dependency_failures(records, allow_editable=False) == [
        (f"External editable/path dependency requires --allow-editable: ../dep ({(tmp_path / 'dep').resolve()})")
    ]
    assert "not a resolvable Git repository" in editable_dependency_failures(records, allow_editable=True)[0]


def test_pixi_dependency_records_append_resolved_editable_git_repos(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    dep = _git_repo(tmp_path / "dep")
    lock_text = f"version: 6\nenvironments:\n  dev:\n    packages:\n      linux-64:\n      - pypi: {dep}\n"

    records = pixi_dependency_records(lock_text, "dev", project)
    repos = repo_paths_with_dependencies(["."], records)

    assert editable_dependency_failures(records, allow_editable=True) == []
    assert str(dep.resolve()) in repos


def test_package_records_include_direct_url(monkeypatch):
    def fake_distribution(name):
        assert name == "example-library"
        return SimpleNamespace(
            metadata={"Name": "example-library"},
            version="0.1.0",
            read_text=lambda filename: (
                json.dumps(
                    {
                        "url": "ssh://github/example-org/example-library.git",
                        "vcs_info": {
                            "vcs": "git",
                            "commit_id": "85d6cc72",
                            "requested_revision": "main",
                        },
                    }
                )
                if filename == "direct_url.json"
                else None
            ),
        )

    monkeypatch.setattr("reprotrail.pixi.importlib.metadata.distribution", fake_distribution)

    assert package_records(("example-library",)) == [
        {
            "requested_name": "example-library",
            "name": "example-library",
            "version": "0.1.0",
            "direct_url": {
                "url": "https://github.com/example-org/example-library",
                "vcs_info": {
                    "vcs": "git",
                    "commit_id": "85d6cc72",
                    "requested_revision": "main",
                },
            },
        }
    ]


def test_package_records_redact_local_file_direct_url(monkeypatch):
    def fake_distribution(name):
        assert name == "reprotrail"
        return SimpleNamespace(
            metadata={"Name": "reprotrail"},
            version="0.1.0",
            read_text=lambda filename: (
                json.dumps(
                    {
                        "url": "file:///workspace/example-project",
                        "dir_info": {"editable": True},
                    }
                )
                if filename == "direct_url.json"
                else None
            ),
        )

    monkeypatch.setattr("reprotrail.pixi.importlib.metadata.distribution", fake_distribution)

    assert package_records(("reprotrail",))[0]["direct_url"] == {
        "url": "<redacted-local-path>",
        "url_kind": "file",
        "path_redacted": True,
        "dir_info": {"editable": True},
    }


def test_environment_summary_contains_runtime_packages(tmp_path, monkeypatch):
    monkeypatch.setattr("reprotrail.pixi.importlib.metadata.version", lambda name: "0.1.0")
    monkeypatch.setattr(
        "reprotrail.pixi.importlib.metadata.distribution",
        lambda name: SimpleNamespace(
            metadata={"Name": name},
            version="0.1.0",
            read_text=lambda filename: (
                json.dumps(
                    {
                        "url": "ssh://github/example-org/example-library.git",
                        "vcs_info": {"vcs": "git", "commit_id": "85d6cc72"},
                    }
                )
                if filename == "direct_url.json"
                else None
            ),
        ),
    )

    summary = environment_summary(
        project_root=tmp_path,
        pixi_environment="analysis",
        dependency_records=[],
        allow_editable=False,
        package_names=("example-library",),
        env_var_whitelist=(),
    )

    assert summary["packages"] == {"example-library": "0.1.0"}
    assert summary["runtime_packages"] == [
        {
            "requested_name": "example-library",
            "name": "example-library",
            "version": "0.1.0",
            "direct_url": {
                "url": "https://github.com/example-org/example-library",
                "vcs_info": {"vcs": "git", "commit_id": "85d6cc72"},
            },
        }
    ]


def test_dependency_snapshot_digest_changes_when_git_package_commit_changes(tmp_path):
    first = epochs.build_dependency_snapshot(
        project_root=tmp_path,
        package_versions_payload={"example-library": "0.1.0"},
        runtime_packages_payload=[
            {
                "requested_name": "example-library",
                "name": "example-library",
                "version": "0.1.0",
                "direct_url": {
                    "url": "https://github.com/example-org/example-library",
                    "vcs_info": {"vcs": "git", "commit_id": "85d6cc72"},
                },
            }
        ],
    )
    changed = epochs.build_dependency_snapshot(
        project_root=tmp_path,
        package_versions_payload={"example-library": "0.1.0"},
        runtime_packages_payload=[
            {
                "requested_name": "example-library",
                "name": "example-library",
                "version": "0.1.0",
                "direct_url": {
                    "url": "https://github.com/example-org/example-library",
                    "vcs_info": {"vcs": "git", "commit_id": "7a69099"},
                },
            }
        ],
    )

    assert first["digest"] != changed["digest"]
    assert epochs.diff_snapshots(first, changed) == ["package example-library source commit changed: 85d6cc72 -> 7a69099"]


def test_dependency_contract_initializes_and_accepts_epochs(tmp_path):
    run_root = tmp_path / "run"
    first = _snapshot("commit-a")
    changed = _snapshot("commit-b")

    result = epochs.check_dependency_contract(
        run_root=run_root,
        project_root=tmp_path,
        snapshot=first,
        dry_run=True,
    )
    assert result["status"] == "would_initialize"
    assert not (run_root / epochs.CONTRACT_RELATIVE_PATH).exists()

    result = epochs.check_dependency_contract(run_root=run_root, project_root=tmp_path, snapshot=first)
    assert result["status"] == "initialized"

    result = epochs.check_dependency_contract(run_root=run_root, project_root=tmp_path, snapshot=first)
    assert result["status"] == "accepted"

    with pytest.raises(RuntimeError, match="Dependency runtime changed"):
        epochs.check_dependency_contract(run_root=run_root, project_root=tmp_path, snapshot=changed)

    result = epochs.check_dependency_contract(
        run_root=run_root,
        project_root=tmp_path,
        snapshot=changed,
        acceptance_reason="validated smoke metrics",
    )
    assert result["status"] == "accepted_new"
    contract = json.loads((run_root / epochs.CONTRACT_RELATIVE_PATH).read_text())
    assert [entry["epoch"] for entry in contract["accepted_snapshots"]] == [1, 2]


def test_audit_dependency_epochs_scans_products(tmp_path):
    run_root = tmp_path / "run"
    first = _snapshot("commit-a")
    second = _snapshot("commit-b")
    epochs.write_json(
        run_root / epochs.CONTRACT_RELATIVE_PATH,
        {
            "schema_version": "1",
            "accepted_snapshots": [
                {"epoch": 1, "accepted_at": "t1", "reason": "initial", "snapshot": first},
                {"epoch": 2, "accepted_at": "t2", "reason": "update", "snapshot": second},
            ],
        },
    )
    for name, snapshot in {"a": first, "b": second}.items():
        epochs.write_json(
            run_root / "products" / name / f"{name}.prov.json",
            {"product": {"data": f"{name}.dat"}, "dependency_snapshot": snapshot},
        )

    report = epochs.audit_dependency_epochs(
        run_root=run_root,
        output=run_root / "qc" / "dependency_epochs.json",
        product_root_markers=("products",),
    )

    assert report["status"] == "report_only"
    assert report["mixed_accepted_epochs"] is True
    assert report["epoch_counts"] == {"1": 1, "2": 1}
