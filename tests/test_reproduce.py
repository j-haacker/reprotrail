from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from reprotrail.reproduce import ReproductionError, reproduce_from_provenance


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _git_output(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_repo(tmp_path: Path, name: str, *, files: dict[str, str] | None = None) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _run(["git", "init"], repo)
    _run(["git", "config", "user.email", "test@example.invalid"], repo)
    _run(["git", "config", "user.name", "Test User"], repo)
    for path, text in (files or {"README.md": name}).items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "initial"], repo)
    _run(["git", "checkout", "-b", "dev"], repo)
    (repo / ".branch").write_text("dev\n", encoding="utf-8")
    _run(["git", "add", ".branch"], repo)
    _run(["git", "commit", "-m", "dev"], repo)
    return repo


def _commit(repo: Path) -> str:
    return _git_output(["rev-parse", "HEAD"], repo)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_provenance(
    run_root: Path,
    *,
    main_repo: Path,
    env_name: str,
    lock_text: str,
    editable: bool = False,
    dep_repo: Path | None = None,
    dep_path: str = "../dep",
) -> Path:
    env_dir = run_root / "provenance" / "environment"
    product_dir = run_root / "products" / "hurs"
    env_dir.mkdir(parents=True)
    product_dir.mkdir(parents=True)
    lock = env_dir / "pixi.lock"
    summary = env_dir / "environment.json"
    lock.write_text(lock_text, encoding="utf-8")
    local_paths = [dep_path] if editable else []
    pixi = {
        "environment": env_name,
        "editable_dependencies": editable,
        "local_path_dependencies": local_paths,
        "local_dependencies": [],
    }
    if editable:
        dependency = {
            "path": dep_path,
            "package": "dep",
            "repo": "dep",
            "kind": "external-editable",
            "local": True,
            "editable": True,
        }
        pixi["local_dependencies"] = [dependency]
        pixi["external_editable_dependencies"] = [dependency]
    summary.write_text(
        json.dumps({"schema_version": "1", "pixi": pixi}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    repos = [
        {
            "name": "main",
            "commit": _commit(main_repo),
            "branch": "dev",
            "remote_url": str(main_repo),
            "dirty": False,
        }
    ]
    if dep_repo is not None:
        repos.append(
            {
                "name": "dep",
                "commit": _commit(dep_repo),
                "branch": "dev",
                "remote_url": str(dep_repo),
                "dirty": False,
            }
        )
    provenance = {
        "schema_version": "1",
        "kind": "reprotrail-run-provenance",
        "artifact_root": {"path": "../.."},
        "command": [
            "python",
            "-m",
            "example",
            "--input",
            "old-input.zarr",
            "--provenance-json",
            "hurs.prov.json",
        ],
        "environment": {
            "manager": "pixi",
            "lockfile": {"path": "provenance/environment/pixi.lock", "sha256": _sha(lock)},
            "summary": {
                "path": "provenance/environment/environment.json",
                "sha256": _sha(summary),
            },
        },
        "product": {"data": "hurs.zarr"},
        "software_repos": repos,
    }
    path = product_dir / "hurs.prov.json"
    path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    digest = _sha(path)
    (product_dir / "hurs.prov.json.sha256").write_text(f"{digest}  hurs.prov.json\n", encoding="utf-8")
    return path


def _rewrite_with_project_repo(provenance: Path) -> None:
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    repos = payload.pop("software_repos")
    payload["project_repo"] = repos[0]
    payload["software_repos"] = repos[1:]
    provenance.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (provenance.parent / f"{provenance.name}.sha256").write_text(
        f"{_sha(provenance)}  {provenance.name}\n",
        encoding="utf-8",
    )


def _rewrite_repo_state(provenance: Path, name: str, **updates: str | None) -> None:
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    repos = [payload.get("project_repo"), *(payload.get("software_repos") or [])]
    repo = next(item for item in repos if item and item.get("name") == name)
    for key, value in updates.items():
        if value is None:
            repo.pop(key, None)
        else:
            repo[key] = value
    provenance.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (provenance.parent / f"{provenance.name}.sha256").write_text(
        f"{_sha(provenance)}  {provenance.name}\n",
        encoding="utf-8",
    )


def _deleted_branch_remote(
    tmp_path: Path,
    name: str,
    *,
    files: dict[str, str] | None = None,
) -> tuple[Path, str, str]:
    source = _git_repo(tmp_path, f"{name}-source", files=files)
    commit = _commit(source)
    remote = tmp_path / f"{name}-remote.git"
    _run(["git", "clone", "--bare", str(source), str(remote)], tmp_path)
    fallback_branch = next(
        branch
        for branch in _git_output(["branch", "--format=%(refname:short)"], source).splitlines()
        if branch != "dev"
    )
    _run(["git", "symbolic-ref", "HEAD", f"refs/heads/{fallback_branch}"], remote)
    _run(["git", "update-ref", "-d", "refs/heads/dev"], remote)
    return source, remote.as_uri(), commit


def test_reproduce_sets_up_production_workspace_without_editable_deps(tmp_path):
    main_repo = _git_repo(tmp_path, "main", files={"pyproject.toml": "[tool.pixi.workspace]\n"})
    provenance = _write_provenance(
        tmp_path / "run",
        main_repo=main_repo,
        env_name="dev",
        lock_text="version: 6\nenvironments:\n  dev:\n    packages: {}\n",
    )

    report = reproduce_from_provenance(
        provenance=provenance,
        workspace=tmp_path / "workspace",
        install=False,
    )

    assert report["status"] == "completed"
    assert report["environment"]["mode"] == "production"
    assert (tmp_path / "workspace" / ".git").exists()
    assert "--provenance-json" not in report["command"]["effective"]
    assert (tmp_path / "workspace" / "reproduction.json").exists()
    assert (tmp_path / "workspace" / "REPRODUCTION.md").exists()


def test_reproduce_resolves_recorded_input_paths(tmp_path, monkeypatch):
    main_repo = _git_repo(tmp_path, "main", files={"pyproject.toml": "[tool.pixi.workspace]\n"})
    input_path = tmp_path / "source-data" / "old-input.zarr"
    input_path.parent.mkdir()
    input_path.write_text("data\n", encoding="utf-8")
    provenance = _write_provenance(
        tmp_path / "run",
        main_repo=main_repo,
        env_name="dev",
        lock_text="version: 6\nenvironments:\n  dev:\n    packages: {}\n",
    )
    payload = json.loads(provenance.read_text(encoding="utf-8"))
    payload["command"] = ["python", "-m", "example", "--input", "source-data/old-input.zarr"]
    payload["input_paths"] = [{"path": "source-data/old-input.zarr"}]
    provenance.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    monkeypatch.chdir(tmp_path)

    report = reproduce_from_provenance(
        provenance=provenance,
        workspace=tmp_path / "workspace",
        install=False,
    )

    assert report["command"]["effective"][-1] == str(input_path)
    assert report["resolved_inputs"] == [
        {
            "path": "source-data/old-input.zarr",
            "resolved": str(input_path),
            "source": "recorded-input-path",
        }
    ]


def test_reproduce_preserves_editable_dependency_paths(tmp_path):
    main_repo = _git_repo(
        tmp_path,
        "main",
        files={
            "pyproject.toml": (
                '[tool.pixi.feature.utils-local.pypi-dependencies]\ndep = { path = "../dep", editable = true }\n'
            )
        },
    )
    dep_repo = _git_repo(tmp_path, "dep")
    provenance = _write_provenance(
        tmp_path / "run",
        main_repo=main_repo,
        dep_repo=dep_repo,
        env_name="dev",
        editable=True,
        lock_text=("version: 6\nenvironments:\n  dev:\n    packages:\n      linux-64:\n      - pypi: ../dep\n"),
    )

    report = reproduce_from_provenance(
        provenance=provenance,
        workspace=tmp_path / "workspace",
        install=False,
    )

    assert report["status"] == "completed"
    assert report["environment"]["mode"] == "editable-local"
    assert (tmp_path / "workspace" / "repos" / "dep" / ".git").exists()
    assert 'path = "repos/dep"' in (tmp_path / "workspace" / "pyproject.toml").read_text()
    assert "- pypi: repos/dep" in (tmp_path / "workspace" / "pixi.lock").read_text()


def test_reproduce_uses_project_repo_with_runtime_software_repos(tmp_path):
    main_repo = _git_repo(
        tmp_path,
        "main",
        files={
            "pyproject.toml": (
                '[tool.pixi.feature.utils-local.pypi-dependencies]\ndep = { path = "../dep", editable = true }\n'
            )
        },
    )
    dep_repo = _git_repo(tmp_path, "dep")
    provenance = _write_provenance(
        tmp_path / "run",
        main_repo=main_repo,
        dep_repo=dep_repo,
        env_name="dev",
        editable=True,
        lock_text=("version: 6\nenvironments:\n  dev:\n    packages:\n      linux-64:\n      - pypi: ../dep\n"),
    )
    _rewrite_with_project_repo(provenance)

    report = reproduce_from_provenance(
        provenance=provenance,
        workspace=tmp_path / "workspace",
        install=False,
    )

    assert report["status"] == "completed"
    assert (tmp_path / "workspace" / ".git").exists()
    assert (tmp_path / "workspace" / "repos" / "dep" / ".git").exists()


def test_reproduce_existing_workspace_requires_resume_or_force(tmp_path):
    main_repo = _git_repo(tmp_path, "main")
    provenance = _write_provenance(
        tmp_path / "run",
        main_repo=main_repo,
        env_name="dev",
        lock_text="version: 6\nenvironments:\n  dev:\n    packages: {}\n",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ReproductionError, match="Workspace already exists"):
        reproduce_from_provenance(
            provenance=provenance,
            workspace=workspace,
            install=False,
        )


def test_reproduce_fetches_recorded_commit_after_remote_branch_is_deleted(tmp_path):
    main_repo, remote_url, commit = _deleted_branch_remote(
        tmp_path,
        "main",
        files={"pyproject.toml": "[tool.pixi.workspace]\n"},
    )
    provenance = _write_provenance(
        tmp_path / "run",
        main_repo=main_repo,
        env_name="dev",
        lock_text="version: 6\nenvironments:\n  dev:\n    packages: {}\n",
    )
    _rewrite_repo_state(provenance, "main", remote_url=remote_url)

    report = reproduce_from_provenance(
        provenance=provenance,
        workspace=tmp_path / "workspace",
        install=False,
    )

    workspace = tmp_path / "workspace"
    assert _commit(workspace) == commit
    assert _git_output(["branch", "--show-current"], workspace) == "dev"
    upstream = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
    )
    assert upstream.returncode != 0
    assert ["git", "fetch", "origin", commit] in [item["command"] for item in report["commands"]]
    clone_command = next(item["command"] for item in report["commands"] if item["step"] == "clone main")
    assert "--no-checkout" in clone_command
    assert "--branch" not in clone_command


def test_reproduce_checks_out_recorded_commit_detached_without_branch(tmp_path):
    main_repo = _git_repo(tmp_path, "main", files={"pyproject.toml": "[tool.pixi.workspace]\n"})
    commit = _commit(main_repo)
    provenance = _write_provenance(
        tmp_path / "run",
        main_repo=main_repo,
        env_name="dev",
        lock_text="version: 6\nenvironments:\n  dev:\n    packages: {}\n",
    )
    _rewrite_repo_state(provenance, "main", branch="detached")

    reproduce_from_provenance(
        provenance=provenance,
        workspace=tmp_path / "workspace",
        install=False,
    )

    workspace = tmp_path / "workspace"
    assert _commit(workspace) == commit
    assert _git_output(["branch", "--show-current"], workspace) == ""


def test_reproduce_fetches_recorded_commit_for_editable_dependency(tmp_path):
    main_repo = _git_repo(
        tmp_path,
        "main",
        files={
            "pyproject.toml": (
                '[tool.pixi.feature.utils-local.pypi-dependencies]\ndep = { path = "../dep", editable = true }\n'
            )
        },
    )
    dep_repo, dep_remote_url, dep_commit = _deleted_branch_remote(tmp_path, "dep")
    provenance = _write_provenance(
        tmp_path / "run",
        main_repo=main_repo,
        dep_repo=dep_repo,
        env_name="dev",
        editable=True,
        lock_text="version: 6\nenvironments:\n  dev:\n    packages:\n      linux-64:\n      - pypi: ../dep\n",
    )
    _rewrite_repo_state(provenance, "dep", remote_url=dep_remote_url)

    report = reproduce_from_provenance(
        provenance=provenance,
        workspace=tmp_path / "workspace",
        install=False,
    )

    restored_dep = tmp_path / "workspace" / "repos" / "dep"
    assert _commit(restored_dep) == dep_commit
    assert _git_output(["branch", "--show-current"], restored_dep) == "dev"
    assert ["git", "fetch", "origin", dep_commit] in [item["command"] for item in report["commands"]]


def test_reproduce_warns_and_falls_back_to_branch_without_recorded_commit(tmp_path):
    main_repo = _git_repo(tmp_path, "main", files={"pyproject.toml": "[tool.pixi.workspace]\n"})
    provenance = _write_provenance(
        tmp_path / "run",
        main_repo=main_repo,
        env_name="dev",
        lock_text="version: 6\nenvironments:\n  dev:\n    packages: {}\n",
    )
    _rewrite_repo_state(provenance, "main", commit=None)

    report = reproduce_from_provenance(
        provenance=provenance,
        workspace=tmp_path / "workspace",
        strict=True,
        install=False,
    )

    assert report["status"] == "failed_strict"
    assert report["warnings"] == ["Repository 'main' has no recorded commit; falling back to branch 'dev'."]
    clone_command = next(item["command"] for item in report["commands"] if item["step"] == "clone main")
    assert clone_command[2:6] == ["--branch", "dev", "--single-branch", str(main_repo)]


def test_reproduce_fails_when_recorded_commit_is_unavailable(tmp_path):
    main_repo = _git_repo(tmp_path, "main", files={"pyproject.toml": "[tool.pixi.workspace]\n"})
    provenance = _write_provenance(
        tmp_path / "run",
        main_repo=main_repo,
        env_name="dev",
        lock_text="version: 6\nenvironments:\n  dev:\n    packages: {}\n",
    )
    missing_commit = "a" * 40
    _rewrite_repo_state(provenance, "main", commit=missing_commit)

    with pytest.raises(
        ReproductionError,
        match=f"Repository 'main' recorded commit {missing_commit}, but it is unavailable from the repository source",
    ):
        reproduce_from_provenance(
            provenance=provenance,
            workspace=tmp_path / "workspace",
            install=False,
        )
