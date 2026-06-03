from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from reprotrail.runner import RunError, run_with_provenance
from reprotrail.settings import ReprotrailSettings


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


def _settings(project, repo):
    return ReprotrailSettings(
        project_root=project,
        repos=(str(repo),),
        product_root_markers=("products",),
        package_summary=("reprotrail",),
        license={
            "spdx": "MIT",
            "name": "MIT License",
            "url": "https://opensource.org/license/mit/",
        },
    )


def _lock(project):
    (project / "pixi.lock").write_text(
        "version: 6\nenvironments:\n  dev:\n    packages:\n      linux-64: []\n",
        encoding="utf-8",
    )


def test_runner_blocks_dirty_repo_before_command(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _lock(project)
    repo = _git_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    provenance = tmp_path / "run" / "product.prov.json"

    with pytest.raises(RunError, match="--allow-dirty"):
        run_with_provenance(
            command=[
                sys.executable,
                "-c",
                "from pathlib import Path; Path('ran').write_text('yes')",
            ],
            log=tmp_path / "run.log",
            provenance_json=provenance,
            settings=_settings(project, repo),
        )

    assert not (tmp_path / "ran").exists()
    payload = json.loads(provenance.read_text())
    assert payload["status"] == "failed_dirty"
    assert payload["software_repos"][0]["dirty"] is True


def test_runner_records_success_environment_and_dirty_patch(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    _lock(project)
    repo = _git_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("not archived\n", encoding="utf-8")
    run_root = tmp_path / "run"
    product = run_root / "products" / "sample" / "sample.dat"
    provenance = product.parent / "sample.prov.json"
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", "dev")
    monkeypatch.setenv("OMP_NUM_THREADS", "7")

    report = run_with_provenance(
        command=[
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                f"p=Path({str(product)!r}); p.parent.mkdir(parents=True, exist_ok=True); "
                "p.write_text('data')"
            ),
        ],
        log=tmp_path / "run.log",
        provenance_json=provenance,
        product_output=product,
        allow_dirty=True,
        settings=_settings(project, repo),
    )

    payload = json.loads(provenance.read_text())
    assert report["status"] == "completed"
    assert payload["status"] == "completed"
    assert payload["returncode"] == 0
    assert payload["environment"]["manager"] == "pixi"
    env_ref = payload["environment"]["summary"]
    env_payload = json.loads((run_root / env_ref["path"]).read_text(encoding="utf-8"))
    assert env_payload["env_vars"]["OMP_NUM_THREADS"] == "7"
    assert "HOME" not in env_payload["env_vars"]
    patch_ref = payload["software_repos"][0]["patch"]
    patch_text = (run_root / patch_ref["path"]).read_text(encoding="utf-8")
    assert "+dirty" in patch_text
    assert "not archived" not in patch_text
    assert payload["software_repos"][0]["untracked_files"] == ["untracked.txt"]
    assert (product.parent / "README.md").exists()


def test_runner_records_signal_failure(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _lock(project)
    repo = _git_repo(tmp_path / "repo")
    provenance = tmp_path / "run" / "signal.prov.json"

    with pytest.raises(SystemExit) as exc:
        run_with_provenance(
            command=[
                sys.executable,
                "-c",
                "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
            ],
            log=tmp_path / "run.log",
            provenance_json=provenance,
            settings=_settings(project, repo),
        )

    assert exc.value.code == 137
    payload = json.loads(provenance.read_text())
    assert payload["status"] == "failed"
    assert payload["returncode"] == -9
    assert payload["exit_status"] == 137
    assert payload["signal"] == "SIGKILL"
    assert "No Python traceback is available" in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_runner_blocks_external_editable_without_allow_editable(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    repo = _git_repo(tmp_path / "repo")
    dep = tmp_path / "dep"
    (project / "pixi.lock").write_text(
        "version: 6\n"
        "environments:\n"
        "  dev:\n"
        "    packages:\n"
        "      linux-64:\n"
        "      - pypi: ../dep\n"
        "packages:\n"
        "- pypi: ../dep\n"
        "  name: dep\n",
        encoding="utf-8",
    )
    os.environ["PIXI_ENVIRONMENT_NAME"] = "dev"

    with pytest.raises(RunError, match="--allow-editable"):
        run_with_provenance(
            command=[sys.executable, "-c", "pass"],
            log=tmp_path / "run.log",
            provenance_json=tmp_path / "run" / "editable.prov.json",
            settings=_settings(project, repo),
        )

    assert not dep.exists()
