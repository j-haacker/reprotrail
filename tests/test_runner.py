from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from reprotrail.reproduce import reproduce_from_provenance
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
    )


def _lock(project):
    (project / "pixi.lock").write_text(
        "version: 6\nenvironments:\n  dev:\n    packages:\n      linux-64: []\n",
        encoding="utf-8",
    )
    if (project / ".git").exists():
        _run(["git", "add", "pixi.lock"], project)
        _run(["git", "commit", "-m", "add lock"], project)


def test_runner_blocks_dirty_repo_before_command(tmp_path):
    project = _git_repo(tmp_path / "project")
    _lock(project)
    (project / "tracked.txt").write_text("dirty\n", encoding="utf-8")
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
            settings=_settings(project, tmp_path / "inactive"),
        )

    assert not (tmp_path / "ran").exists()
    payload = json.loads(provenance.read_text())
    assert payload["status"] == "failed_dirty"
    assert payload["project_repo"]["dirty"] is True


def test_runner_records_success_environment_and_dirty_patch(tmp_path, monkeypatch):
    monkeypatch.setattr("reprotrail.product_metadata.pixi_package_license_records", lambda *_args: [])
    project = _git_repo(tmp_path / "project")
    _lock(project)
    (project / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    (project / "untracked.txt").write_text("not archived\n", encoding="utf-8")
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
        settings=_settings(project, tmp_path / "inactive"),
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
    patch_ref = payload["project_repo"]["patch"]
    patch_text = (run_root / patch_ref["path"]).read_text(encoding="utf-8")
    assert "+dirty" in patch_text
    assert "not archived" not in patch_text
    assert payload["project_repo"]["untracked_files"] == ["untracked.txt"]
    assert (product.parent / "README.md").exists()


def test_runner_records_signal_failure(tmp_path):
    project = _git_repo(tmp_path / "project")
    _lock(project)
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
            settings=_settings(project, tmp_path / "inactive"),
        )

    assert exc.value.code == 137
    payload = json.loads(provenance.read_text())
    assert payload["status"] == "failed"
    assert payload["returncode"] == -9
    assert payload["exit_status"] == 137
    assert payload["signal"] == "SIGKILL"
    assert "No Python traceback is available" in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_runner_records_declared_inputs_before_and_after_command(tmp_path):
    project = _git_repo(tmp_path / "project")
    _lock(project)
    inputs = [tmp_path / "source-a.txt", tmp_path / "source-b.txt"]
    for index, input_path in enumerate(inputs):
        input_path.write_text(f"source {index}\n", encoding="utf-8")
    provenance = tmp_path / "run" / "declared.prov.json"
    observed = tmp_path / "observed-inputs.json"

    report = run_with_provenance(
        command=[
            sys.executable,
            "-c",
            (
                "import json; from pathlib import Path; "
                f"p=Path({str(provenance)!r}); "
                f"Path({str(observed)!r}).write_text(json.dumps(json.loads(p.read_text())['input_paths']))"
            ),
        ],
        log=tmp_path / "run.log",
        provenance_json=provenance,
        inputs=inputs,
        settings=_settings(project, tmp_path / "inactive"),
    )

    initial_inputs = json.loads(observed.read_text(encoding="utf-8"))
    expected_paths = [str(path.resolve()) for path in inputs]
    assert [item["path"] for item in initial_inputs] == expected_paths
    assert [item["path"] for item in report["input_paths"]] == expected_paths


def test_runner_returns_declared_inputs_without_a_sidecar_path(tmp_path):
    project = _git_repo(tmp_path / "project")
    _lock(project)
    declared = tmp_path / "declared.txt"
    declared.write_text("declared\n", encoding="utf-8")

    report = run_with_provenance(
        command=[sys.executable, "-c", "pass"],
        log=tmp_path / "run.log",
        inputs=[declared],
        settings=_settings(project, tmp_path / "inactive"),
    )

    assert report["status"] == "completed"
    assert report["input_paths"][0]["path"] == str(declared.resolve())


def test_runner_keeps_declared_and_child_added_inputs(tmp_path):
    project = _git_repo(tmp_path / "project")
    _lock(project)
    declared = tmp_path / "declared.txt"
    child_added = tmp_path / "child-added.txt"
    declared.write_text("declared\n", encoding="utf-8")
    child_added.write_text("child\n", encoding="utf-8")
    provenance = tmp_path / "run" / "merged.prov.json"

    report = run_with_provenance(
        command=[
            sys.executable,
            "-c",
            (
                "import json; from pathlib import Path; "
                f"p=Path({str(provenance)!r}); payload=json.loads(p.read_text()); "
                f"payload['input_paths']=[{{'path': {str(child_added)!r}, 'exists': True, "
                "'kind': 'file', 'backend': 'filesystem'}]; "
                "p.write_text(json.dumps(payload))"
            ),
        ],
        log=tmp_path / "run.log",
        provenance_json=provenance,
        inputs=[declared],
        settings=_settings(project, tmp_path / "inactive"),
    )

    assert [item["path"] for item in report["input_paths"]] == [
        str(declared.resolve()),
        str(child_added.resolve()),
    ]


def test_runner_merges_duplicate_input_metadata_without_losing_git_state(tmp_path):
    project = _git_repo(tmp_path / "project")
    _lock(project)
    declared = project / "tracked.txt"
    equivalent_path = project / ".." / "project" / "tracked.txt"
    provenance = tmp_path / "run" / "merged.prov.json"

    report = run_with_provenance(
        command=[
            sys.executable,
            "-c",
            (
                "import json; from pathlib import Path; "
                f"p=Path({str(provenance)!r}); payload=json.loads(p.read_text()); "
                f"payload['input_paths']=[{{'path': {str(equivalent_path)!r}, 'exists': True, "
                "'kind': 'file', 'backend': 'filesystem', "
                "'metadata': {'selection': {'variable': 'tas'}}}]; "
                "p.write_text(json.dumps(payload))"
            ),
        ],
        log=tmp_path / "run.log",
        provenance_json=provenance,
        inputs=[declared],
        settings=_settings(project, tmp_path / "inactive"),
    )

    assert len(report["input_paths"]) == 1
    input_record = report["input_paths"][0]
    assert input_record["backend"] == "git"
    assert input_record["git"]["name"] == "project"
    assert input_record["metadata"]["selection"] == {"variable": "tas"}


def test_runner_retains_missing_declared_input_when_command_fails(tmp_path):
    project = _git_repo(tmp_path / "project")
    _lock(project)
    missing = tmp_path / "missing.nc"
    provenance = tmp_path / "run" / "failed.prov.json"

    with pytest.raises(SystemExit) as exc:
        run_with_provenance(
            command=[sys.executable, "-c", "raise SystemExit(7)"],
            log=tmp_path / "run.log",
            provenance_json=provenance,
            inputs=[missing],
            settings=_settings(project, tmp_path / "inactive"),
        )

    assert exc.value.code == 7
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["input_paths"] == [
        {
            "path": str(missing.resolve()),
            "exists": False,
            "kind": "missing",
            "backend": "unknown",
        }
    ]


def test_declared_product_input_is_resolved_and_verified_during_reproduction(tmp_path, monkeypatch):
    project = _git_repo(tmp_path / "project")
    _lock(project)
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", "dev")
    product_dir = tmp_path / "products" / "effective-config"
    product_dir.mkdir(parents=True)
    effective_config = product_dir / "effective-config.json"
    effective_config.write_text('{"region": "AT"}\n', encoding="utf-8")
    input_provenance = product_dir / "effective-config.prov.json"
    input_provenance.write_text('{"schema_version": "1"}\n', encoding="utf-8")
    input_digest = hashlib.sha256(input_provenance.read_bytes()).hexdigest()
    (product_dir / "effective-config.prov.json.sha256").write_text(
        f"{input_digest}  effective-config.prov.json\n",
        encoding="utf-8",
    )
    report_provenance = tmp_path / "run" / "products" / "report" / "report.prov.json"

    run_with_provenance(
        command=[sys.executable, "-c", "pass", str(effective_config)],
        log=tmp_path / "report.log",
        provenance_json=report_provenance,
        inputs=[effective_config],
        settings=_settings(project, tmp_path / "inactive"),
    )

    recorded = json.loads(report_provenance.read_text(encoding="utf-8"))
    assert recorded["input_paths"][0]["metadata"]["product_provenance"] == {
        "path": "effective-config.prov.json",
        "sha256": input_digest,
    }
    reproduction = reproduce_from_provenance(
        provenance=report_provenance,
        workspace=tmp_path / "workspace",
        repo_sources={"project": str(project)},
        install=False,
    )

    assert reproduction["resolved_inputs"] == [
        {
            "path": str(effective_config),
            "resolved": str(effective_config),
            "source": "recorded-input-path",
        }
    ]
    input_validation = next(
        item for item in reproduction["validations"] if item["name"] == "input product provenance 0"
    )
    assert input_validation["status"] == "ok"
    assert input_validation["expected_sha256"] == input_digest


def test_runner_blocks_external_editable_without_allow_editable(tmp_path):
    project = _git_repo(tmp_path / "project")
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
            settings=_settings(project, tmp_path / "inactive"),
        )

    assert not dep.exists()


def test_runner_does_not_record_inactive_configured_repo_as_software_repo(tmp_path):
    project = _git_repo(tmp_path / "project")
    _lock(project)
    inactive = _git_repo(tmp_path / "inactive")
    provenance = tmp_path / "run" / "remote.prov.json"

    run_with_provenance(
        command=[sys.executable, "-c", "pass"],
        log=tmp_path / "run.log",
        provenance_json=provenance,
        settings=_settings(project, inactive),
    )

    payload = json.loads(provenance.read_text())
    assert payload["project_repo"]["name"] == "project"
    assert payload["software_repos"] == []
    assert [repo["name"] for repo in payload["configured_repos"]] == ["inactive"]


def test_runner_records_active_editable_dependency_repo(tmp_path, monkeypatch):
    project = _git_repo(tmp_path / "project")
    _git_repo(tmp_path / "dep")
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
    _run(["git", "add", "pixi.lock"], project)
    _run(["git", "commit", "-m", "add lock"], project)
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", "dev")
    provenance = tmp_path / "run" / "editable.prov.json"

    run_with_provenance(
        command=[sys.executable, "-c", "pass"],
        log=tmp_path / "run.log",
        provenance_json=provenance,
        allow_editable=True,
        settings=_settings(project, project),
    )

    payload = json.loads(provenance.read_text())
    assert payload["project_repo"]["name"] == "project"
    assert [repo["name"] for repo in payload["software_repos"]] == ["dep"]
    assert payload["configured_repos"] == []
