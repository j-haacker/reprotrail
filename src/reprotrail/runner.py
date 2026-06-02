"""Command runner that records provenance and runtime status."""

from __future__ import annotations

import argparse
import hashlib
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._json import read_json, write_json
from ._paths import relative_path
from .epochs import (
    annotate_product_environment_consistency,
    build_dependency_snapshot,
    contract_epoch_for_snapshot,
)
from .pixi import (
    editable_dependency_failures,
    infer_pixi_environment,
    pixi_dependency_records,
    repo_paths_with_dependencies,
    write_environment_bundle,
)
from .products import finalize_product_provenance, product_record, product_sidecars
from .provenance import get_git_state, public_git_state, run_git
from .settings import ReprotrailSettings, load_settings


class RunError(RuntimeError):
    """Raised when the runner cannot start or finish safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def command_value(command: list[str], flag: str) -> str | None:
    skip_next = False
    prefix = f"{flag}="
    for index, part in enumerate(command):
        if skip_next:
            skip_next = False
            continue
        if part == flag:
            return command[index + 1] if index + 1 < len(command) else None
        if part.startswith(prefix):
            return part.removeprefix(prefix)
    return None


def clean_command(command: list[str]) -> list[str]:
    cleaned = []
    skip_next = False
    for part in command:
        if skip_next:
            skip_next = False
            continue
        if part in {"--provenance-json", "--reprotrail-provenance-json"}:
            skip_next = True
            continue
        if part.startswith(("--provenance-json=", "--reprotrail-provenance-json=")):
            continue
        cleaned.append(part)
    return cleaned


def default_provenance_path(
    command: list[str], product_output: str | Path | None = None
) -> Path | None:
    output = product_output or command_value(command, "--output")
    if not output:
        return None
    return product_sidecars(output).provenance


def infer_run_root(provenance_path: Path | None, product_root_markers: tuple[str, ...]) -> Path | None:
    if provenance_path is None:
        return None
    path = provenance_path.resolve()
    for parent in path.parents:
        if parent.name in set(product_root_markers):
            return parent.parent
    return path.parent


def _git_output(repo: Path, args: list[str]) -> str:
    ok, output, error = run_git(args, cwd=repo)
    if not ok:
        raise RuntimeError(error or f"git {' '.join(args)} failed")
    return output


def _tracked_patch_text(repo: Path) -> str:
    staged = _git_output(repo, ["diff", "--cached", "--binary", "--no-ext-diff", "--"])
    unstaged = _git_output(repo, ["diff", "--binary", "--no-ext-diff", "--"])
    return f"{staged}{unstaged}"


def _untracked_files(status_short: str) -> list[str]:
    return [
        line[3:]
        for line in status_short.splitlines()
        if line.startswith("?? ") and len(line) > 3
    ]


def _write_dirty_patch_refs(states: list[dict[str, Any]], run_root: Path | None) -> None:
    if run_root is None:
        return
    patch_dir = run_root / "provenance" / "software" / "patches"
    for state in states:
        if not state.get("dirty"):
            continue
        status_short = str(state.get("status_short") or "")
        untracked = _untracked_files(status_short)
        if untracked:
            state["untracked_files"] = untracked
        repo_label = state.get("_repo_root") or state.get("label")
        if not repo_label:
            continue
        patch_text = _tracked_patch_text(Path(str(repo_label)))
        if not patch_text:
            state.pop("diff_hash", None)
            continue
        patch_bytes = patch_text.encode("utf-8", errors="replace")
        digest = hashlib.sha256(patch_bytes).hexdigest()
        patch_dir.mkdir(parents=True, exist_ok=True)
        patch_path = patch_dir / f"{digest}.patch"
        if not patch_path.exists():
            patch_path.write_bytes(patch_bytes)
        state["diff_hash"] = digest
        state["patch"] = {
            "path": relative_path(patch_path, run_root),
            "sha256": digest,
            "kind": "git-diff",
            "tracked_only": True,
        }


def collect_software_states(repos: list[str], log: Path) -> list[dict[str, Any]]:
    states = []
    for repo in repos:
        repo_path = Path(repo)
        if not repo_path.exists():
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"WARNING: {repo} unavailable; provenance skipped.\n")
            continue
        try:
            git_state = get_git_state(repo_path)
            state = public_git_state(git_state)
            state["label"] = repo
            state["_repo_root"] = str(git_state.repo_root)
        except Exception as err:
            with log.open("a", encoding="utf-8") as handle:
                handle.write(f"WARNING: {repo} provenance failed: {err}\n")
            continue
        states.append(state)
    return states


def public_record(payload: dict[str, Any]) -> dict[str, Any]:
    record = dict(payload)
    if "software_repos" in record:
        record["software_repos"] = [
            public_git_state(state) for state in record.get("software_repos") or []
        ]
    return record


def write_provenance(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    write_json(path, public_record(payload))


def read_provenance(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return read_json(path)


def dirty_failures(states: list[dict[str, Any]]) -> list[str]:
    failures = []
    for state in states:
        if not state.get("dirty"):
            continue
        label = state.get("label") or state.get("name") or state.get("_repo_root")
        status = state.get("status_short") or "(dirty; status unavailable)"
        failures.append(f"{label} is dirty:\n{status}")
    return failures


def failure_payload(returncode: int) -> dict[str, Any]:
    if returncode == 0:
        return {"exit_status": 0}
    if returncode < 0:
        signum = -returncode
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = f"SIG{signum}"
        exit_status = 128 + signum
        return {
            "exit_status": exit_status,
            "signal": signal_name,
            "signal_number": signum,
            "error": (
                f"Command terminated by signal {signum} ({signal_name}); "
                f"shell exit status {exit_status}. No Python traceback is available "
                "for a process killed by this signal."
            ),
        }
    return {
        "exit_status": returncode,
        "error": f"Command failed with exit code {returncode}.",
    }


def _artifact_root_payload(run_root: Path | None, provenance_path: Path | None) -> dict[str, Any] | None:
    if run_root is None or provenance_path is None:
        return None
    return {"path": relative_path(run_root, provenance_path.parent)}


def run_with_provenance(
    *,
    command: list[str],
    log: str | Path,
    repos: list[str] | None = None,
    allow_dirty: bool = False,
    allow_editable: bool = False,
    provenance_json: str | Path | None = None,
    product_output: str | Path | None = None,
    settings: ReprotrailSettings | None = None,
) -> dict[str, Any]:
    """Run a command while recording v1 reprotrail provenance."""

    if not command:
        raise RunError("No command provided.")
    settings = settings or load_settings()
    project_root = settings.project_root
    log_path = Path(log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path = (
        Path(provenance_json)
        if provenance_json
        else default_provenance_path(command, product_output)
    )
    run_root = infer_run_root(provenance_path, settings.product_root_markers)
    product_output = product_output or command_value(command, "--output")
    clean = clean_command(command)
    pixi_environment = infer_pixi_environment(project_root, settings.pixi_environment)
    lockfile = project_root / settings.pixi_lockfile
    lock_text = lockfile.read_text(encoding="utf-8") if lockfile.exists() else ""
    dependency_records = (
        pixi_dependency_records(lock_text, pixi_environment, project_root)
        if lock_text
        else []
    )

    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"COMMAND: {' '.join(command)}\n\n")

    inspected_repos = repo_paths_with_dependencies(
        repos or list(settings.repos), dependency_records
    )
    software_states = collect_software_states(inspected_repos, log_path)
    environment_refs: dict[str, Any] = {}
    warnings: list[str] = []
    if run_root is not None and lockfile.exists():
        environment_refs = write_environment_bundle(
            run_root=run_root,
            project_root=project_root,
            lockfile=lockfile,
            pixi_environment=pixi_environment,
            dependency_records=dependency_records,
            allow_editable=allow_editable,
            package_names=settings.package_summary,
            env_var_whitelist=settings.env_var_whitelist,
        )
        environment_refs["manager"] = "pixi"
    elif not lockfile.exists():
        warnings.append(f"Pixi lockfile not found: {lockfile}")

    start_payload: dict[str, Any] = {
        "schema_version": "1",
        "kind": "reprotrail-run-provenance",
        "status": "started",
        "started_at": utc_now(),
        "command": clean,
        "allow_dirty": allow_dirty,
        "allow_editable": allow_editable,
        "software_repos": software_states,
        "input_paths": [],
        "warnings": warnings,
    }
    artifact_root = _artifact_root_payload(run_root, provenance_path)
    if artifact_root:
        start_payload["artifact_root"] = artifact_root
    if environment_refs:
        start_payload["environment"] = environment_refs
    if product_output is not None:
        start_payload["product"] = product_record(
            product_output, provenance_path=provenance_path
        )
    if settings.license:
        start_payload["license"] = settings.license

    dirty = dirty_failures(software_states)
    editable = editable_dependency_failures(
        dependency_records, allow_editable=allow_editable
    )
    dirty_blocked = bool(dirty and not allow_dirty)
    if dirty_blocked or editable:
        messages = []
        if dirty_blocked:
            messages.append(
                "Dirty software repository state requires --allow-dirty.\n\n"
                + "\n\n".join(dirty)
            )
        if editable:
            messages.append(
                "Editable/path dependency provenance is incomplete or disallowed.\n\n"
                + "\n".join(editable)
            )
        message = "\n\n".join(messages)
        failed_payload = {
            **start_payload,
            "status": "failed_dirty" if dirty_blocked else "failed_editable",
            "error": message,
        }
        write_provenance(provenance_path, failed_payload)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
        raise RunError(message)

    _write_dirty_patch_refs(software_states, run_root)
    start_payload["software_repos"] = software_states
    dependency_snapshot = None
    dependency_epoch = None
    if lockfile.exists():
        dependency_snapshot = build_dependency_snapshot(
            project_root=project_root,
            lockfile=lockfile,
            pixi_environment=pixi_environment,
            dependency_records=dependency_records,
            software_states=software_states,
            package_names=settings.package_summary,
        )
        start_payload["dependency_snapshot"] = dependency_snapshot
        dependency_epoch = contract_epoch_for_snapshot(run_root, dependency_snapshot)
        if dependency_epoch:
            start_payload["dependency_epoch"] = {
                "epoch": dependency_epoch.get("epoch"),
                "accepted_at": dependency_epoch.get("accepted_at"),
                "reason": dependency_epoch.get("reason"),
            }
    write_provenance(provenance_path, start_payload)

    with log_path.open("a", encoding="utf-8") as handle:
        proc = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, text=True)
    failure = failure_payload(proc.returncode)
    if proc.returncode != 0:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n" + failure["error"] + "\n")

    end_payload = read_provenance(provenance_path) or start_payload
    end_payload.update(
        {
            "status": "completed" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
            "ended_at": utc_now(),
            **failure,
        }
    )
    if dependency_snapshot and "dependency_snapshot" not in end_payload:
        end_payload["dependency_snapshot"] = dependency_snapshot
    if dependency_epoch and "dependency_epoch" not in end_payload:
        end_payload["dependency_epoch"] = {
            "epoch": dependency_epoch.get("epoch"),
            "accepted_at": dependency_epoch.get("accepted_at"),
            "reason": dependency_epoch.get("reason"),
        }
    write_provenance(provenance_path, end_payload)

    if proc.returncode == 0 and provenance_path is not None and dependency_snapshot:
        consistency = annotate_product_environment_consistency(
            provenance_path,
            run_root=run_root,
            output_snapshot=dependency_snapshot,
            output_epoch=dependency_epoch,
        )
        if consistency:
            end_payload = read_provenance(provenance_path)
    if proc.returncode == 0 and provenance_path is not None:
        try:
            finalize_product_provenance(provenance_path, license=settings.license)
            end_payload = read_provenance(provenance_path)
        except Exception as err:
            end_payload.setdefault("warnings", []).append(
                f"Product provenance finalization failed: {err}"
            )
            write_provenance(provenance_path, end_payload)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"WARNING: product provenance finalization failed: {err}\n")
    if proc.returncode != 0:
        raise SystemExit(failure["exit_status"])
    return end_payload


def run_from_namespace(args: argparse.Namespace) -> dict[str, Any]:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return run_with_provenance(
        command=command,
        log=args.log,
        repos=args.repo,
        allow_dirty=args.allow_dirty,
        allow_editable=args.allow_editable,
        provenance_json=args.provenance_json,
        product_output=args.product_output,
    )
