"""Reproduce a recorded provenance step in a fresh workspace."""

from __future__ import annotations

import copy
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ._json import canonical_json, read_json, write_json
from ._paths import sha256_file, sha256_text
from .provenance import canonicalize_remote_url, clean_command_parts


class ReproductionError(RuntimeError):
    """Raised when a reproduction setup cannot proceed."""


def parse_key_value(items: list[str] | None) -> dict[str, str]:
    result = {}
    for item in items or []:
        if "=" not in item:
            raise ReproductionError(f"Expected KEY=VALUE, got {item!r}.")
        key, value = item.split("=", 1)
        if not key or not value:
            raise ReproductionError(f"Expected KEY=VALUE, got {item!r}.")
        result[key] = value
    return result


def _artifact_root(record: dict[str, Any], provenance_path: Path) -> Path:
    artifact = record.get("artifact_root") or {}
    path = artifact.get("path")
    if path:
        value = Path(str(path))
        return value if value.is_absolute() else (provenance_path.parent / value).resolve()
    return provenance_path.parent


def _resolve_run_ref(record: dict[str, Any], provenance_path: Path, ref: str | None) -> Path | None:
    if not ref:
        return None
    path = Path(ref)
    if path.is_absolute():
        return path
    return _artifact_root(record, provenance_path) / path


def _parse_sha256_sidecar(text: str) -> str | None:
    parts = text.strip().split()
    return parts[0] if parts else None


def _validation(
    report: dict[str, Any],
    *,
    name: str,
    path: Path,
    expected_sha256: str | None,
    required: bool = False,
) -> bool:
    item: dict[str, Any] = {
        "name": name,
        "path": str(path),
        "expected_sha256": expected_sha256,
    }
    if not path.exists():
        item["status"] = "missing"
        report["validations"].append(item)
        message = f"{name} is missing: {path}"
        (report["blockers"] if required else report["warnings"]).append(message)
        return False
    actual = sha256_file(path)
    item["actual_sha256"] = actual
    item["status"] = "ok" if not expected_sha256 or actual == expected_sha256 else "mismatch"
    report["validations"].append(item)
    if expected_sha256 and actual != expected_sha256:
        message = f"{name} checksum mismatch: {path}"
        (report["blockers"] if required else report["warnings"]).append(message)
        return False
    return True


def apply_input_maps(command: list[str], input_maps: dict[str, str]) -> list[str]:
    return [input_maps.get(part, part) for part in command]


def _resolve_existing_recorded_path(value: str, provenance_path: Path) -> Path | None:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve() if path.exists() else None
    bases = [Path.cwd().resolve(), provenance_path.parent, *provenance_path.parents]
    for base in dict.fromkeys(bases):
        candidate = base / path
        if candidate.exists():
            return candidate.resolve()
    return None


def resolve_input_maps(
    *,
    record: dict[str, Any],
    provenance_path: Path,
    explicit_maps: dict[str, str],
    report: dict[str, Any],
) -> dict[str, str]:
    resolved_maps = dict(explicit_maps)
    report["resolved_inputs"] = []
    for item in record.get("input_paths") or []:
        original = str(item.get("path") or "")
        if not original or original in resolved_maps:
            continue
        resolved = _resolve_existing_recorded_path(original, provenance_path)
        if resolved is None:
            report["warnings"].append(f"Recorded input path could not be resolved: {original}")
            continue
        resolved_maps[original] = str(resolved)
        report["resolved_inputs"].append(
            {
                "path": original,
                "resolved": str(resolved),
                "source": "recorded-input-path",
            }
        )
    return resolved_maps


def _repo_name(state: dict[str, Any]) -> str:
    name = state.get("name") or state.get("repo")
    if name:
        return str(name)
    remote = str(state.get("remote_url") or "").rstrip("/")
    if remote:
        return remote.removesuffix(".git").split("/")[-1]
    return "repo"


def _select_project_repo(
    repos: list[dict[str, Any]],
    project_repo: str | None,
    recorded_project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if recorded_project:
        if not project_repo or _repo_name(recorded_project) == project_repo:
            return recorded_project
    if project_repo:
        for state in repos:
            if _repo_name(state) == project_repo:
                return state
        raise ReproductionError(f"Project repo {project_repo!r} not found in provenance.")
    if not repos:
        raise ReproductionError("Provenance contains neither project_repo nor legacy software_repos project entries.")
    return repos[0]


def _git_value(repo: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _repo_contains_commit(repo: Path, commit: str | None) -> bool:
    if not commit:
        return False
    return _git_value(repo, ["cat-file", "-t", commit]) == "commit"


def _candidate_local_repos(name: str) -> list[Path]:
    cwd = Path.cwd().resolve()
    candidates = []
    if cwd.name == name:
        candidates.append(cwd)
    candidates.append(cwd.parent / name)
    return list(dict.fromkeys(candidates))


def _matching_local_repo_source(state: dict[str, Any]) -> str | None:
    name = _repo_name(state)
    commit = state.get("commit")
    for candidate in _candidate_local_repos(name):
        if not (candidate / ".git").exists():
            continue
        if not _repo_contains_commit(candidate, commit):
            continue
        return str(candidate)
    return None


def _repo_source(state: dict[str, Any], sources: dict[str, str]) -> str | None:
    name = _repo_name(state)
    return sources.get(name) or _matching_local_repo_source(state) or state.get("remote_url")


def _branch_name(state: dict[str, Any]) -> str | None:
    branch = state.get("branch")
    if not branch or branch == "detached":
        return None
    return str(branch)


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    report: dict[str, Any],
    step: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    item = {"step": step, "command": command, "cwd": str(cwd) if cwd else None}
    report["commands"].append(item)
    proc = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **({"env": env} if env is not None else {}),
    )
    item["returncode"] = proc.returncode
    if proc.stdout:
        item["stdout"] = proc.stdout[-4000:]
    if proc.stderr:
        item["stderr"] = proc.stderr[-4000:]
    if check and proc.returncode != 0:
        raise ReproductionError(f"{step} failed with exit code {proc.returncode}: {shlex.join(command)}")
    return proc


def _clone_or_resume_repo(
    *,
    state: dict[str, Any],
    destination: Path,
    sources: dict[str, str],
    report: dict[str, Any],
    resume: bool,
    reuse_existing: bool = False,
) -> bool:
    name = _repo_name(state)
    source = _repo_source(state, sources)
    if not source:
        raise ReproductionError(f"Repository {name!r} has no remote_url; provide --repo-source {name}=PATH_OR_URL.")
    branch = _branch_name(state)
    commit = state.get("commit")
    if not commit:
        fallback = f"branch {branch!r}" if branch else "the source's default branch"
        report["warnings"].append(f"Repository {name!r} has no recorded commit; falling back to {fallback}.")
    if destination.exists():
        head = _git_value(destination, ["rev-parse", "HEAD"]) or None
        if reuse_existing and (not commit or head == commit):
            report["adaptations"].append(
                {
                    "kind": "existing-editable-dependency",
                    "repo": name,
                    "path": str(destination),
                    "commit": commit,
                }
            )
            report["repos"].append(
                {
                    "name": name,
                    "source": source,
                    "path": str(destination),
                    "commit": commit,
                    "branch": _branch_name(state),
                    "dirty": bool(state.get("dirty")),
                    "reused_existing": True,
                }
            )
            return False
        if reuse_existing and commit and head != commit:
            raise ReproductionError(
                f"Editable dependency path already exists at {destination}, "
                f"but HEAD is {head} instead of recorded commit {commit}."
            )
        if not resume:
            raise ReproductionError(f"Repository destination already exists: {destination}")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        clone_command = ["git", "clone"]
        if commit:
            clone_command.append("--no-checkout")
        elif branch:
            clone_command.extend(["--branch", branch, "--single-branch"])
        clone_command.extend([source, str(destination)])
        _run(clone_command, report=report, step=f"clone {name}")
    if commit:
        if not _repo_contains_commit(destination, str(commit)):
            _run(
                ["git", "fetch", "origin", str(commit)],
                cwd=destination,
                report=report,
                step=f"fetch recorded commit for {name}",
                check=False,
            )
        if not _repo_contains_commit(destination, str(commit)):
            raise ReproductionError(
                f"Repository {name!r} recorded commit {commit}, "
                f"but it is unavailable from the repository source {source!r}."
            )
        if branch:
            _run(
                ["git", "checkout", "--no-track", "-B", branch, str(commit)],
                cwd=destination,
                report=report,
                step=f"checkout {name}",
            )
            if _git_value(destination, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]):
                _run(
                    ["git", "branch", "--unset-upstream"],
                    cwd=destination,
                    report=report,
                    step=f"unset upstream for {name}",
                )
        else:
            _run(
                ["git", "checkout", "--detach", str(commit)],
                cwd=destination,
                report=report,
                step=f"checkout {name}",
            )
    elif not branch:
        _run(
            ["git", "fetch", "--all", "--tags", "--prune"],
            cwd=destination,
            report=report,
            step=f"fetch {name}",
        )
    report["repos"].append(
        {
            "name": name,
            "source": source,
            "path": str(destination),
            "commit": commit,
            "branch": branch,
            "dirty": bool(state.get("dirty")),
        }
    )
    return True


def _copy_artifact(source: Path | None, destination_dir: Path, report: dict[str, Any], *, name: str) -> Path | None:
    if source is None or not source.exists():
        return None
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    shutil.copy2(source, destination)
    report["artifacts"].append({"name": name, "source": str(source), "path": str(destination)})
    return destination


def _apply_patch_if_present(
    *,
    state: dict[str, Any],
    record: dict[str, Any],
    provenance_path: Path,
    repo_path: Path,
    artifact_dir: Path,
    report: dict[str, Any],
) -> None:
    patch = state.get("patch") or {}
    patch_path = _resolve_run_ref(record, provenance_path, patch.get("path"))
    if patch_path is None:
        return
    if not _validation(
        report,
        name=f"patch {_repo_name(state)}",
        path=patch_path,
        expected_sha256=patch.get("sha256"),
        required=True,
    ):
        return
    copied = _copy_artifact(patch_path, artifact_dir / "patches", report, name=f"patch {_repo_name(state)}")
    if copied is not None:
        _run(
            ["git", "apply", str(copied)],
            cwd=repo_path,
            report=report,
            step=f"apply patch {_repo_name(state)}",
        )
        report["patches"].append({"repo": _repo_name(state), "path": str(copied), "sha256": patch.get("sha256")})


def _environment_refs(record: dict[str, Any], provenance_path: Path) -> tuple[Path | None, Path | None, dict[str, Any]]:
    environment = record.get("environment") or {}
    lock_ref = environment.get("lockfile") or {}
    summary_ref = environment.get("summary") or {}
    lock_path = _resolve_run_ref(record, provenance_path, lock_ref.get("path"))
    summary_path = _resolve_run_ref(record, provenance_path, summary_ref.get("path"))
    return lock_path, summary_path, environment


def _load_environment_summary(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return read_json(path)


def _external_editable_dependencies(
    *, summary: dict[str, Any], pixi: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    local_records = pixi.get("local_dependencies") or []
    external = [dict(record) for record in local_records if record.get("kind") == "external-editable"]
    local_paths = [str(record.get("path")) for record in local_records if record.get("path")]
    if pixi.get("editable_dependencies") and not local_records:
        schema = summary.get("schema_version") or "unknown"
        raise ReproductionError(
            "Editable-local provenance requires environment schema v1 with detailed "
            f"local dependency records; found schema {schema}."
        )
    return external, local_paths


def _replace_path_token(text: str, old: str, new: str) -> str:
    replacements = {old: new, f'"{old}"': f'"{new}"', f"'{old}'": f"'{new}'"}
    for source, target in sorted(replacements.items(), key=lambda item: -len(item[0])):
        text = text.replace(source, target)
    return text


def _rewrite_editable_paths(*, project_path: Path, replacements: dict[str, str], report: dict[str, Any]) -> None:
    if not replacements:
        return
    pyproject_path = project_path / "pyproject.toml"
    if not pyproject_path.exists():
        raise ReproductionError("Reproduction from editable sources needs pyproject.toml.")
    old_text = pyproject_path.read_text(encoding="utf-8")
    new_text = old_text
    for old, new in replacements.items():
        new_text = _replace_path_token(new_text, old, new)
    lock_path = project_path / "pixi.lock"
    if lock_path.exists():
        old_lock_text = lock_path.read_text(encoding="utf-8")
        new_lock_text = old_lock_text
        for old, new in replacements.items():
            new_lock_text = _replace_path_token(new_lock_text, old, new)
        if new_lock_text != old_lock_text:
            lock_path.write_text(new_lock_text, encoding="utf-8")
    if new_text == old_text:
        return
    pyproject_path.write_text(new_text, encoding="utf-8")
    report["adaptations"].append(
        {
            "kind": "editable-path-rewrite",
            "path": str(pyproject_path),
            "old_sha256": sha256_text(old_text),
            "new_sha256": sha256_text(new_text),
            "replacements": [{"old": old, "new": new} for old, new in sorted(replacements.items())],
        }
    )


def _verify_input_provenance(*, record: dict[str, Any], input_maps: dict[str, str], report: dict[str, Any]) -> None:
    for index, item in enumerate(record.get("input_paths") or []):
        metadata = item.get("metadata") or {}
        product_provenance = metadata.get("product_provenance") or {}
        ref = product_provenance.get("path")
        expected = product_provenance.get("sha256")
        if not ref:
            continue
        original_input = str(item.get("path") or "")
        mapped_input = input_maps.get(original_input, original_input)
        sidecar = Path(mapped_input).parent / ref
        _validation(
            report,
            name=f"input product provenance {index}",
            path=sidecar,
            expected_sha256=expected,
            required=False,
        )


def _write_markdown_report(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Reproduction Report",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Environment mode: `{report.get('environment', {}).get('mode', 'unknown')}`",
        f"- Workspace: `{report.get('workspace')}`",
        "",
        "## Command",
        "",
        "```bash",
        shlex.join(report.get("command", {}).get("effective", [])),
        "```",
        "",
        "## Warnings",
        "",
    ]
    lines.extend([f"- {item}" for item in report.get("warnings") or []] or ["- None"])
    lines.extend(["", "## Blockers", ""])
    lines.extend([f"- {item}" for item in report.get("blockers") or []] or ["- None"])
    lines.extend(["", "## Repositories", ""])
    repos = report.get("repos") or []
    lines.extend(
        [f"- {repo.get('name')}: `{repo.get('commit')}` at `{repo.get('path')}`" for repo in repos] or ["- None"]
    )
    if report.get("recursive"):
        lines.extend(["", "## Recursive steps", ""])
        lines.extend(f"- {step['id']}: {step['status']}" for step in report["steps"])
        lines.extend(["", "See reproduction.json for commands, identity evidence, gaps, and substitutions."])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def reproduce_from_provenance(
    *,
    provenance: str | Path,
    workspace: str | Path,
    execute: bool = False,
    strict: bool = False,
    env: str | None = None,
    project_repo: str | None = None,
    repo_sources: dict[str, str] | None = None,
    input_maps: dict[str, str] | None = None,
    resume: bool = False,
    force: bool = False,
    install: bool = True,
    recursive: bool = False,
    bundle: str | Path | None = None,
    accept_incomplete: bool = False,
    input_roots: dict[str, str] | None = None,
    _record: dict[str, Any] | None = None,
    _output_root: Path | None = None,
) -> dict[str, Any]:
    """Create a reproduction workspace from a product provenance sidecar."""

    if recursive or bundle is not None:
        return _reproduce_recursive(
            provenance=provenance,
            workspace=workspace,
            execute=execute,
            strict=strict,
            env=env,
            project_repo=project_repo,
            repo_sources=repo_sources or {},
            input_maps=input_maps or {},
            resume=resume,
            force=force,
            install=install,
            bundle=bundle,
            accept_incomplete=accept_incomplete,
            input_roots=input_roots,
        )
    provenance_path = Path(provenance).resolve()
    workspace_path = Path(workspace).resolve()
    repo_sources = repo_sources or {}
    input_maps = input_maps or {}
    report: dict[str, Any] = {
        "schema_version": "1",
        "status": "started",
        "provenance": str(provenance_path),
        "workspace": str(workspace_path),
        "execute": execute,
        "strict": strict,
        "warnings": [],
        "blockers": [],
        "validations": [],
        "artifacts": [],
        "repos": [],
        "patches": [],
        "adaptations": [],
        "commands": [],
        "input_maps": input_maps,
    }
    record = _record if _record is not None else read_json(provenance_path)
    report["product"] = record.get("product") or {}
    resolved_input_maps = resolve_input_maps(
        record=record,
        provenance_path=provenance_path,
        explicit_maps=input_maps,
        report=report,
    )
    report["input_maps"] = resolved_input_maps
    recorded_command = record.get("command") or []
    command = clean_command_parts(recorded_command)
    report["command"] = {
        "recorded": recorded_command,
        "cleaned": command,
        "effective": apply_input_maps(command, resolved_input_maps),
    }
    sidecar = provenance_path.with_suffix(f"{provenance_path.suffix}.sha256")
    if sidecar.exists():
        expected = _parse_sha256_sidecar(sidecar.read_text(encoding="utf-8"))
        _validation(
            report,
            name="product provenance",
            path=provenance_path,
            expected_sha256=expected,
            required=strict,
        )
    else:
        report["warnings"].append(f"Product provenance checksum sidecar is missing: {sidecar}")

    lock_source, summary_source, environment_ref = _environment_refs(record, provenance_path)
    summary = _load_environment_summary(summary_source)
    pixi = summary.get("pixi") or {}
    env_name = env or pixi.get("environment") or (summary.get("env_vars") or {}).get("PIXI_ENVIRONMENT_NAME")
    if not env_name:
        raise ReproductionError("Pixi environment is not recorded; provide --env NAME.")
    if lock_source is None:
        raise ReproductionError("Provenance does not reference a pixi lockfile.")
    _validation(
        report,
        name="pixi lockfile",
        path=lock_source,
        expected_sha256=(environment_ref.get("lockfile") or {}).get("sha256"),
        required=True,
    )
    if summary_source is not None:
        _validation(
            report,
            name="environment summary",
            path=summary_source,
            expected_sha256=(environment_ref.get("summary") or {}).get("sha256"),
            required=False,
        )

    external_dependencies, local_paths = _external_editable_dependencies(summary=summary, pixi=pixi)
    editable = bool(external_dependencies)
    report["environment"] = {
        "name": env_name,
        "mode": "editable-local" if editable else "production",
        "editable_dependencies": editable,
        "local_path_dependencies": local_paths,
        "external_editable_dependencies": external_dependencies,
    }
    _verify_input_provenance(record=record, input_maps=resolved_input_maps, report=report)

    if workspace_path.exists():
        if force:
            shutil.rmtree(workspace_path)
        elif not resume:
            raise ReproductionError(f"Workspace already exists: {workspace_path}. Use --resume or --force.")

    repos = record.get("software_repos") or []
    project = _select_project_repo(
        repos,
        project_repo,
        recorded_project=record.get("project_repo") if isinstance(record.get("project_repo"), dict) else None,
    )
    repo_root = workspace_path / "repos"
    project_path = workspace_path
    report["project_repo_path"] = str(project_path)
    artifact_dir = workspace_path / "provenance-source"
    project_was_restored = _clone_or_resume_repo(
        state=project,
        destination=project_path,
        sources=repo_sources,
        report=report,
        resume=resume,
    )
    if project_was_restored:
        _apply_patch_if_present(
            state=project,
            record=record,
            provenance_path=provenance_path,
            repo_path=project_path,
            artifact_dir=artifact_dir,
            report=report,
        )
    _copy_artifact(provenance_path, artifact_dir, report, name="product provenance")
    _copy_artifact(
        sidecar if sidecar.exists() else None,
        artifact_dir,
        report,
        name="product provenance checksum",
    )
    copied_lock = _copy_artifact(lock_source, artifact_dir / "environment", report, name="pixi lockfile")
    _copy_artifact(summary_source, artifact_dir / "environment", report, name="environment summary")
    if copied_lock is not None:
        shutil.copy2(copied_lock, project_path / "pixi.lock")

    repo_by_name = {_repo_name(state): state for state in repos}
    if editable:
        path_rewrites: dict[str, str] = {}
        for dependency in external_dependencies:
            local_path = str(dependency.get("path") or "")
            name = str(dependency.get("repo") or Path(local_path).name)
            state = repo_by_name.get(name)
            if state is None:
                report["blockers"].append(f"Editable dependency {name!r} is missing from software_repos.")
                continue
            destination = repo_root / name
            path_rewrites[local_path] = f"repos/{name}"
            dependency_was_restored = _clone_or_resume_repo(
                state=state,
                destination=destination,
                sources=repo_sources,
                report=report,
                resume=resume,
                reuse_existing=True,
            )
            if dependency_was_restored:
                _apply_patch_if_present(
                    state=state,
                    record=record,
                    provenance_path=provenance_path,
                    repo_path=destination,
                    artifact_dir=artifact_dir,
                    report=report,
                )
        _rewrite_editable_paths(project_path=project_path, replacements=path_rewrites, report=report)

    try:
        if _record is not None:
            for state in record.get("input_paths", []):
                if state.get("backend") == "git" and not (state.get("metadata") or {}).get("product_provenance"):
                    source = Path(resolved_input_maps.get(state["path"], state["path"]))
                    valid, _ = _input_identity(source, state)
                    if not valid:
                        report["blockers"].append(f"Restored configuration missing or changed: {source}")
        if strict and (report["warnings"] or report["blockers"]):
            report["status"] = "failed_strict"
        elif report["blockers"]:
            report["status"] = "blocked"
        else:
            if install:
                git_environment = None
                if _record is not None:
                    git_environment = _archived_git_environment(
                        record,
                        repo_sources,
                        project_path / "pixi.lock",
                        artifact_dir,
                        report,
                    )
                _run(
                    ["pixi", "install", "--locked", "-e", str(env_name)],
                    cwd=project_path,
                    report=report,
                    step="pixi install",
                    **({"env": git_environment} if git_environment else {}),
                )
            if execute:
                if _record is not None:
                    tokens = report["command"]["effective"]
                    for index, token in enumerate(tokens):
                        flag, equal, value = token.partition("=")
                        if flag in {"--output", "--qc", "--done", "--validation-json", "--provenance-json"}:
                            output_value = value if equal else tokens[index + 1]
                            output_parent = Path(output_value).parent.resolve()
                            if not output_parent.is_relative_to(_output_root or workspace_path):
                                raise ReproductionError(f"Output escapes reproduction workspace: {output_value}")
                            output_parent.mkdir(parents=True, exist_ok=True)
                proc = _run(
                    ["pixi", "run", "-e", str(env_name), *report["command"]["effective"]],
                    cwd=project_path,
                    report=report,
                    step="execute command",
                )
                report["execution"] = {"returncode": proc.returncode}
            report["status"] = "completed"
    except Exception as err:
        report["status"] = "failed"
        report["blockers"].append(str(err))
        write_json(workspace_path / "reproduction.json", report)
        _write_markdown_report(workspace_path / "REPRODUCTION.md", report)
        raise
    write_json(workspace_path / "reproduction.json", report)
    _write_markdown_report(workspace_path / "REPRODUCTION.md", report)
    return report


def _archived_git_environment(
    record: dict[str, Any],
    sources: dict[str, str],
    lock: Path,
    artifact_dir: Path,
    report: dict[str, Any],
) -> dict[str, str] | None:
    """Route locked Git downloads to explicitly archived sources without editing the lock."""
    from .export import traced_repositories

    urls = {
        token.split("?", 1)[0].split("#", 1)[0].removeprefix("git+")
        for token in re.findall(r"git\+[^\s\"'<>]+", lock.read_text())
    }
    replacements = {}
    for repo in traced_repositories(record):
        source = sources.get(_repo_name(repo))
        if not source or not Path(source).is_file():
            continue
        remote = canonicalize_remote_url(repo.get("remote_url"))
        matches = [url for url in urls if canonicalize_remote_url(url) == remote]
        if not matches:
            continue
        bare = artifact_dir / "git" / (_repo_name(repo) + ".git")
        if not bare.exists():
            _run(["git", "clone", "--bare", source, str(bare)], report=report, step="restore archived Git dependency")
        replacements.update({url: bare.as_uri() for url in matches})
    if not replacements:
        return None
    environment = dict(os.environ)
    offset = int(environment.get("GIT_CONFIG_COUNT", "0"))
    for index, (remote, local) in enumerate(sorted(replacements.items()), start=offset):
        environment[f"GIT_CONFIG_KEY_{index}"] = f"url.{local}.insteadOf"
        environment[f"GIT_CONFIG_VALUE_{index}"] = remote
    environment["GIT_CONFIG_COUNT"] = str(offset + len(replacements))
    report["adaptations"].append({"kind": "archived-git-dependencies", "sources": replacements})
    return environment


def _input_identity(path: Path, state: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Check only recorded evidence; never imply that a stat manifest hashes content."""
    from .provenance import summarize_directory

    if not path.exists():
        return False, {"path": str(path), "exists": False}
    metadata = state.get("metadata") or {}
    evidence: dict[str, Any] = {"path": str(path), "exists": True}
    if path.is_file():
        stat = path.stat()
        evidence.update(size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns)
        expected = metadata.get("file") or {}
        if expected.get("sha256"):
            evidence["sha256"] = sha256_file(path)
            evidence["strength"] = "content-sha256"
            return evidence["sha256"] == expected["sha256"], evidence
        evidence["strength"] = "size-mtime" if expected else "existence-only"
        return all(evidence.get(k) == expected[k] for k in ("size_bytes", "mtime_ns") if k in expected), evidence
    expected = metadata.get("directory") or {}
    observed = summarize_directory(path, max_entries=expected.get("max_entries", 20_000))
    evidence["directory"] = observed
    evidence["strength"] = "paths-size-mtime" if expected.get("manifest_hash") else "existence-only"
    return not expected.get("manifest_hash") or observed["manifest_hash"] == expected["manifest_hash"], evidence


def _replay_command(
    record: dict[str, Any],
    node: dict[str, Any],
    inputs: dict[str, str],
    output: Path,
    step: Path,
) -> list[str]:
    """Relocate complete path tokens, never arbitrary substrings inside JSON or text."""
    aliases = dict(inputs)
    for item in node["inputs"]:
        if item["recorded"] in inputs and item.get("source"):
            aliases[item["source"]] = inputs[item["recorded"]]
    aliases[node["data"]] = str(output)
    command = clean_command_parts(record["command"])
    writable = {"--output", "--qc", "--done", "--validation-json", "--provenance-json"}
    result = []
    for index, token in enumerate(command):
        flag, equal, value = token.partition("=")
        previous = command[index - 1] if index else ""
        if previous in writable or (equal and flag in writable):
            original = value if equal else token
            target = (
                output
                if (flag if equal else previous) == "--output"
                else step / "auxiliary" / ((flag if equal else previous).removeprefix("--") + "-" + Path(original).name)
            )
            if (flag if equal else previous) == "--provenance-json":
                target = output.parent / Path(node["provenance"]).name
            result.append(f"{flag}={target}" if equal else str(target))
            continue
        raw = value if equal and flag.startswith("--") else token
        candidates = {
            local
            for original, local in aliases.items()
            if raw == original or ("/" in original and raw.endswith("/" + original.lstrip("/")))
        }
        if len(candidates) > 1:
            raise ReproductionError(f"Ambiguous command path {raw}; provide --input-map RECORDED=LOCAL.")
        if candidates:
            replacement = candidates.pop()
            result.append(f"{flag}={replacement}" if raw != token else replacement)
            continue
        # Relative scripts/configs inside the restored checkout are allowed. Escaping
        # paths must be mapped, even for commands originating on this same host.
        if raw.startswith("/") or (not raw.startswith("-") and ".." in Path(raw).parts):
            raise ReproductionError(f"Unmapped command path {raw}; provide --input-map RECORDED=LOCAL.")
        result.append(token)
    return result


def _reproduce_recursive(
    *,
    provenance: str | Path,
    workspace: str | Path,
    execute: bool,
    strict: bool,
    env: str | None,
    project_repo: str | None,
    repo_sources: dict[str, str],
    input_maps: dict[str, str],
    resume: bool,
    force: bool,
    install: bool,
    bundle: str | Path | None,
    accept_incomplete: bool,
    input_roots: dict[str, str] | None,
) -> dict[str, Any]:
    from graphlib import CycleError, TopologicalSorter

    from .export import ExportError, artifact_path, discover_products, load_export, traced_repositories

    work = Path(workspace).resolve()
    original_target = Path(provenance).absolute()
    try:
        if bundle is None:
            graph = discover_products([provenance], input_roots=input_roots)
            bundle_root = None
            target = graph["roots"][0]
        else:
            graph, bundle_root = load_export(bundle)
            matches = [
                key
                for key, node in graph["products"].items()
                if str(original_target) == node["provenance"]
                or str(original_target) == str(bundle_root / graph["files"][node["provenance"]]["path"])
                or str(provenance) == graph["files"][node["provenance"]]["path"]
            ]
            if len(matches) != 1:
                raise ExportError("Target must identify exactly one provenance record in the export.")
            target = matches[0]
    except (ExportError, ValueError, OSError) as exc:
        raise ReproductionError(str(exc)) from exc

    def local_file(original: str) -> Path:
        ref = graph["files"].get(original)
        return bundle_root / ref["path"] if bundle_root is not None and ref else Path(original)

    def mapped_input(entry: dict[str, Any], fallback: Path) -> Path:
        if entry["recorded"] in input_maps:
            return Path(input_maps[entry["recorded"]]).resolve()
        name = (entry["state"].get("git") or {}).get("name")
        if name in (input_roots or {}):
            relative = Path(entry["recorded"])
            if relative.is_absolute():
                old_root = graph["input_roots"].get(name)
                if not old_root or not relative.is_relative_to(old_root):
                    return fallback
                relative = relative.relative_to(old_root)
            return Path(input_roots[name]).resolve() / relative
        return fallback

    reusable: dict[str, tuple[Path, list[dict[str, Any]]]] = {}

    def existing_dependency(identity: str) -> bool:
        node = graph["products"][identity]
        data = (
            bundle_root / node["data_path"]
            if bundle_root and node.get("data_path")
            else (
                local_file(node["provenance"]).parent / Path(node["data"]).name if bundle_root else Path(node["data"])
            )
        )
        consumers = [
            entry
            for consumer in graph["products"].values()
            for entry in consumer["inputs"]
            if entry.get("dependency") == identity
        ]
        evidence = []
        candidates = set()
        for entry in consumers:
            candidate = mapped_input(entry, data)
            okay, observed = _input_identity(candidate, entry["state"])
            evidence.append(observed)
            sidecar = candidate.parent / entry["state"]["metadata"]["product_provenance"]["path"]
            if not okay or not sidecar.is_file() or sha256_file(sidecar) != identity:
                return False
            candidates.add(candidate)
        if len(candidates) == 1:
            reusable[identity] = (candidates.pop(), evidence)
            return True
        return False

    needed: set[str] = set()

    def require(identity: str) -> None:
        if identity in needed:
            return
        needed.add(identity)
        if identity != target and existing_dependency(identity):
            return
        for dep in graph["products"][identity]["dependencies"]:
            require(dep)

    try:
        require(target)
        order = list(
            TopologicalSorter(
                {i: [d for d in graph["products"][i]["dependencies"] if d in needed] for i in needed}
            ).static_order()
        )
    except (CycleError, KeyError) as exc:
        raise ReproductionError(f"Invalid dependency graph: {exc}") from exc
    # Stable ordering also makes plans and resume fingerprints deterministic.
    rank = {identity: index for index, identity in enumerate(graph["order"])}
    order.sort(key=lambda identity: rank[identity])
    report: dict[str, Any] = {
        "schema_version": "1",
        "status": "planned",
        "workspace": str(work),
        "recursive": True,
        "target": target,
        "order": order,
        "steps": [],
        "substitutions": [],
        "warnings": [],
        "blockers": [],
    }
    if force:
        resume = False
    previous = read_json(work / "reproduction.json") if resume and (work / "reproduction.json").is_file() else {}
    if work.exists() and not resume:
        if force:
            # Never delete a bundle, original data tree, or enclosing directory.
            protected = [original_target.resolve()] + [Path(n["data"]).resolve() for n in graph["products"].values()]
            protected.extend(Path(p).resolve() for p in graph["files"])
            protected.extend(Path(e["source"]).resolve() for e in graph["external_inputs"] if e.get("source"))
            protected.extend(Path(p).resolve() for p in repo_sources.values() if Path(p).exists())
            if bundle_root is not None:
                protected.append(bundle_root)
            if any(p.is_relative_to(work) for p in protected):
                raise ReproductionError("Workspace encloses source data or metadata; refusing --force.")
            shutil.rmtree(work)
        else:
            raise ReproductionError(f"Workspace already exists: {work}. Use --resume or --force.")
    work.mkdir(parents=True, exist_ok=True)
    available: dict[str, Path] = {}
    regenerated: set[str] = set()
    old_steps = {s["id"]: s for s in previous.get("steps", [])}
    verified_archives: dict[tuple[str, int, int], str] = {}

    def save() -> dict[str, Any]:
        write_json(work / "reproduction.json", report)
        _write_markdown_report(work / "REPRODUCTION.md", report)
        return report

    for identity in order:
        node = graph["products"][identity]
        item: dict[str, Any] = {"id": identity, "status": "planned", "inputs": [], "warnings": []}
        report["steps"].append(item)
        provenance_path = local_file(node["provenance"])
        try:
            if not provenance_path.is_file() or sha256_file(provenance_path) != identity:
                raise ReproductionError(f"Provenance missing or changed: {provenance_path}")
            archived_record = read_json(provenance_path)
            record = copy.deepcopy(archived_record)
            original_provenance = Path(node["provenance"])
            if identity in reusable:
                source, evidence = reusable[identity]
                item["inputs"] = evidence
                available[identity] = source
                item["status"] = "reused"
                continue
            gaps = [g for g in graph["gaps"] if g.get("product") == identity or not g.get("product")]
            if gaps:
                item["warnings"].extend(gaps)
                if not accept_incomplete:
                    raise ReproductionError(f"Step {identity} has metadata gaps; inspect or use --accept-incomplete.")
            maps = dict(input_maps)
            overlays: dict[Path, Path] = {}
            for entry in node["inputs"]:
                dependency = entry.get("dependency")
                if dependency:
                    source = available.get(dependency)
                    if source is None:
                        if execute:
                            raise ReproductionError(f"Dependency did not produce data: {dependency}")
                        source = work / "products" / dependency / Path(graph["products"][dependency]["data"]).name
                    if dependency in regenerated:
                        parent_ref = entry["state"]["metadata"]["product_provenance"]
                        sidecar = source.parent / parent_ref["path"]
                        if not sidecar.is_file():
                            raise ReproductionError(f"Regenerated dependency lacks provenance: {sidecar}")
                        new_digest = sha256_file(sidecar)
                        for state in record.get("input_paths", []):
                            if state["path"] == entry["recorded"]:
                                state["metadata"]["product_provenance"]["sha256"] = new_digest
                        report["substitutions"].append(
                            {
                                "original": dependency,
                                "regenerated": new_digest,
                                "data": str(source),
                                "consumer": identity,
                            }
                        )
                else:
                    source = mapped_input(entry, local_file(entry.get("source", entry["recorded"])))
                    # Tracked project files are restored from the producer's revision.
                    state = entry["state"]
                    project = record.get("project_repo") or {}
                    if state.get("backend") == "git" and (state.get("git") or {}).get("name") == project.get("name"):
                        if not Path(entry["recorded"]).is_absolute() and entry["recorded"] not in maps:
                            source = work / "steps" / identity / entry["recorded"]
                    else:
                        valid, evidence = _input_identity(source, state)
                        item["inputs"].append(evidence)
                        if not valid:
                            raise ReproductionError(f"External input missing or changed: {source}")
                if dependency and source.exists():
                    _, evidence = _input_identity(source, entry["state"])
                    item["inputs"].append(evidence)
                maps[entry["recorded"]] = str(source)
                name = (entry["state"].get("git") or {}).get("name")
                root = graph["input_roots"].get(name)
                recorded_path = Path(entry["recorded"])
                if root and (not recorded_path.is_absolute() or recorded_path.is_relative_to(root)):
                    relative = recorded_path.relative_to(root) if recorded_path.is_absolute() else recorded_path
                    if ".." in relative.parts:
                        raise ReproductionError(f"Input path escapes repository: {relative}")
                    overlay = work / "inputs" / identity / name
                    overlays[overlay / relative] = source
                    maps[str(Path(root))] = str(overlay)
                    # Catalog-driven producers use a data root instead of individual file arguments.
                    tokens = record["command"]
                    for index, token in enumerate(tokens):
                        data_root = (
                            tokens[index + 1]
                            if token == "--data-root" and index + 1 < len(tokens)
                            else (token.split("=", 1)[1] if token.startswith("--data-root=") else None)
                        )
                        if data_root and (Path(data_root).name == name or data_root == root):
                            maps[data_root] = str(overlay)
            output = work / "products" / identity / Path(node["data"]).name
            step = work / "steps" / identity
            item["output"] = str(output)
            # Command and all ancillary writes are contained in this workspace.
            command = _replay_command(record, node, maps, output, step)
            item["command"] = command
            record["command"] = command
            for role, ref in (record.get("environment") or {}).items():
                if not isinstance(ref, dict) or not ref.get("path"):
                    continue
                original = artifact_path(archived_record, original_provenance, ref["path"]).resolve()
                artifact = local_file(str(original))
                if not artifact.is_file():
                    raise ReproductionError(f"Required environment artifact missing: {artifact}")
                actual = sha256_file(artifact)
                if ref.get("sha256") and actual != ref["sha256"]:
                    item["warnings"].append(f"{role} hash mismatch: {artifact}")
                    if not accept_incomplete:
                        raise ReproductionError(f"Environment artifact changed: {artifact}")
                    ref["sha256"] = actual
                ref["path"] = str(artifact)
            sources = dict(repo_sources)
            for repo in graph["repositories"]:
                archive = repo.get("archive")
                if archive and bundle_root is not None:
                    path = bundle_root / archive["path"]
                    if not path.is_file():
                        raise ReproductionError(f"Repository archive missing: {path}")
                    stat = path.stat()
                    cache_key = (str(path), stat.st_size, stat.st_mtime_ns)
                    if cache_key not in verified_archives:
                        verified_archives[cache_key] = sha256_file(path)
                    if verified_archives[cache_key] != archive["sha256"]:
                        raise ReproductionError(f"Repository archive changed: {path}")
                    sources.setdefault(_repo_name(repo), str(path))
            for repo in traced_repositories(record):
                patch = repo.get("patch") or {}
                if patch.get("path"):
                    patch_path = local_file(
                        str(
                            artifact_path(
                                archived_record,
                                original_provenance,
                                patch["path"],
                            ).resolve()
                        )
                    )
                    if not patch_path.is_file():
                        raise ReproductionError(f"Required patch missing: {patch_path}")
                    actual = sha256_file(patch_path)
                    if patch.get("sha256") and actual != patch["sha256"]:
                        item["warnings"].append(f"Patch hash mismatch: {patch_path}")
                        if not accept_incomplete:
                            raise ReproductionError(f"Required patch changed: {patch_path}")
                        patch["sha256"] = actual
                    patch["path"] = str(patch_path)
            # Include source/config metadata, observed input state and replay options.
            signature = sha256_text(
                canonical_json(
                    {
                        "record": record,
                        "maps": maps,
                        "inputs": item["inputs"],
                        "env": env,
                        "sources": sources,
                        "strict": strict,
                        "accept_incomplete": accept_incomplete,
                        "project_repo": project_repo,
                        "install": install,
                    }
                )
            )
            item["signature"] = signature
            old = old_steps.get(identity, {})
            if resume and old.get("signature") == signature and old.get("status") in {"completed", "resumed"}:
                valid, evidence = _input_identity(output, old.get("output_state", {}))
                old_sidecar = old.get("output_provenance")
                if old_sidecar:
                    sidecar = Path(old_sidecar["path"])
                    valid = valid and sidecar.is_file() and sha256_file(sidecar) == old_sidecar["sha256"]
                if valid:
                    item.update(
                        status="resumed", output_state=old.get("output_state", {}), output_provenance=old_sidecar
                    )
                    available[identity] = output
                    regenerated.add(identity)
                    continue
            if not execute:
                item["status"] = "planned"
                available[identity] = output
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            # A failed or invalidated step gets a fresh checkout and fresh products.
            if step.exists():
                shutil.rmtree(step)
            if output.exists():
                if output.is_dir():
                    shutil.rmtree(output)
                else:
                    output.unlink()
            for link, source in overlays.items():
                link.parent.mkdir(parents=True, exist_ok=True)
                if link.is_symlink():
                    link.unlink()
                if link.exists():
                    raise ReproductionError(f"Input overlay collision: {link}")
                link.symlink_to(source, target_is_directory=source.is_dir())
            result = reproduce_from_provenance(
                provenance=provenance_path,
                workspace=step,
                execute=True,
                strict=strict,
                env=env,
                project_repo=project_repo,
                repo_sources=sources,
                input_maps=maps,
                install=install,
                _record=record,
                _output_root=work,
            )
            if result["status"] != "completed" or not output.exists():
                raise ReproductionError(f"Producer failed or output missing: {output}")
            from .provenance import summarize_directory

            item["output_state"] = (
                {"metadata": {"directory": summarize_directory(output)}}
                if output.is_dir()
                else {"metadata": {"file": {"sha256": sha256_file(output)}}}
            )
            generated_sidecar = output.parent / Path(node["provenance"]).name
            if generated_sidecar.is_file():
                item["output_provenance"] = {"path": str(generated_sidecar), "sha256": sha256_file(generated_sidecar)}
            item["status"] = "completed"
            available[identity] = output
            regenerated.add(identity)
            save()
        except (ReproductionError, ExportError, OSError, ValueError) as exc:
            item["status"] = "blocked"
            report["blockers"].append(str(exc))
            report["status"] = "blocked"
            if execute:
                return save()
    report["status"] = "blocked" if report["blockers"] else ("completed" if execute else "planned")
    return save()
