"""Dependency snapshot contracts and product environment audits."""

from __future__ import annotations

import hashlib
import os
import platform
from pathlib import Path
from typing import Any

from ._json import canonical_json, read_json, write_json
from ._paths import sha256_file
from .pixi import (
    git_value,
    package_records,
    package_versions,
    pixi_dependency_records,
    public_dependency_records,
)

CONTRACT_RELATIVE_PATH = Path("provenance/dependency_contract.json")
README_NOTE_START = "<!-- reprotrail-runtime-environment-note:start -->"
README_NOTE_END = "<!-- reprotrail-runtime-environment-note:end -->"


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_digest(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def git_diff_hash(repo: Path) -> str | None:
    staged = git_value(repo, ["diff", "--cached", "--binary", "--no-ext-diff", "--"])
    unstaged = git_value(repo, ["diff", "--binary", "--no-ext-diff", "--"])
    patch = f"{staged}{unstaged}"
    if not patch:
        return None
    return hashlib.sha256(patch.encode("utf-8", errors="replace")).hexdigest()


def git_state(repo: Path) -> dict[str, Any]:
    status = git_value(repo, ["status", "--porcelain"])
    state = {
        "repo_root": str(repo.resolve()),
        "repo": repo.resolve().name,
        "commit": git_value(repo, ["rev-parse", "HEAD"]),
        "branch": git_value(repo, ["branch", "--show-current"]) or None,
        "remote_url": git_value(repo, ["remote", "get-url", "origin"]) or None,
        "dirty": bool(status),
    }
    if status:
        state["status_short"] = status
        state["status_hash"] = hashlib.sha256(status.encode("utf-8")).hexdigest()
        diff_hash = git_diff_hash(repo)
        if diff_hash:
            state["diff_hash"] = diff_hash
    return {key: value for key, value in state.items() if value not in (None, "")}


def state_identity(state: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "repo",
        "name",
        "commit",
        "branch",
        "remote_url",
        "dirty",
        "diff_hash",
        "status_hash",
        "status_short",
    )
    return {key: state[key] for key in keys if state.get(key) not in (None, "")}


def state_name(state: dict[str, Any]) -> str | None:
    return state.get("repo") or state.get("name") or Path(str(state.get("repo_root") or state.get("label") or "")).name


def package_record_name(record: dict[str, Any]) -> str:
    return str(record.get("requested_name") or record.get("name") or "unknown")


def package_source_identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "requested_name": record.get("requested_name"),
        "name": record.get("name"),
        "version": record.get("version"),
        "direct_url": record.get("direct_url"),
        "direct_url_error": record.get("direct_url_error"),
    }


def package_source_commit(record: dict[str, Any]) -> Any:
    return ((record.get("direct_url") or {}).get("vcs_info") or {}).get("commit_id")


def package_source_url(record: dict[str, Any]) -> Any:
    return (record.get("direct_url") or {}).get("url")


def software_state_for_dependency(
    record: dict[str, Any], software_states: list[dict[str, Any]]
) -> dict[str, Any] | None:
    repo_root = record.get("_repo_root")
    repo = record.get("repo")
    package = record.get("package")
    for state in software_states:
        state_root = state.get("repo_root") or state.get("label")
        if repo_root and state_root:
            try:
                if Path(str(state_root)).resolve() == Path(str(repo_root)).resolve():
                    return state
            except OSError:
                pass
        name = state_name(state)
        if name and name in {repo, package}:
            return state
    return None


def build_dependency_snapshot(
    *,
    project_root: Path,
    lockfile: Path | None = None,
    pixi_environment: str | None = None,
    package_versions_payload: dict[str, str] | None = None,
    runtime_packages_payload: list[dict[str, Any]] | None = None,
    dependency_records: list[dict[str, Any]] | None = None,
    software_states: list[dict[str, Any]] | None = None,
    package_names: tuple[str, ...] = ("reprotrail",),
) -> dict[str, Any]:
    """Build a stable dependency/runtime snapshot."""

    project_root = project_root.resolve()
    lockfile = lockfile or project_root / "pixi.lock"
    lock_text = lockfile.read_text(encoding="utf-8") if lockfile.exists() else ""
    records = dependency_records
    if records is None:
        records = pixi_dependency_records(lock_text, pixi_environment, project_root)
    external_records = [record for record in records if record.get("kind") == "external-editable"]
    states = software_states or []
    editable_dependencies = []
    for record in external_records:
        state = software_state_for_dependency(record, states)
        if state is None and record.get("_repo_root"):
            state = git_state(Path(str(record["_repo_root"])))
        dependency = {
            "path": record.get("path"),
            "package": record.get("package"),
            "repo": record.get("repo"),
            "kind": record.get("kind"),
        }
        if state is not None:
            dependency["git"] = state_identity(state)
        editable_dependencies.append({key: value for key, value in dependency.items() if value not in (None, "", {})})
    payload = {
        "schema_version": "1",
        "kind": "reprotrail-dependency-snapshot",
        "pixi": {
            "environment": pixi_environment,
            "lockfile": {
                "path": os.path.relpath(lockfile, project_root),
                "sha256": sha256_file(lockfile) if lockfile.exists() else None,
            },
            "local_path_dependencies": [
                str(record.get("path")) for record in public_dependency_records(records) if record.get("path")
            ],
        },
        "packages": package_versions_payload or package_versions(package_names),
        "runtime_packages": (
            runtime_packages_payload if runtime_packages_payload is not None else package_records(package_names)
        ),
        "editable_dependencies": editable_dependencies,
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
        },
    }
    payload = dict(sorted(payload.items()))
    payload["digest"] = stable_digest(payload)
    return payload


def dependency_state_signature(snapshot: dict[str, Any]) -> dict[str, Any]:
    pixi = snapshot.get("pixi") or {}
    lockfile = pixi.get("lockfile") or {}
    deps = []
    for dep in snapshot.get("editable_dependencies") or []:
        git = dep.get("git") or {}
        deps.append(
            {
                "package": dep.get("package"),
                "repo": dep.get("repo"),
                "commit": git.get("commit"),
                "dirty": git.get("dirty", False),
                "diff_hash": git.get("diff_hash"),
                "status_hash": git.get("status_hash"),
                "status_short": git.get("status_short"),
            }
        )
    return {
        "lockfile_sha256": lockfile.get("sha256"),
        "pixi_environment": pixi.get("environment"),
        "runtime_packages": sorted(
            [package_source_identity(record) for record in snapshot.get("runtime_packages") or []],
            key=canonical_json,
        ),
        "editable_dependencies": sorted(deps, key=canonical_json),
    }


def snapshots_equivalent(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not left or not right:
        return False
    if left.get("digest") and left.get("digest") == right.get("digest"):
        return True
    return dependency_state_signature(left) == dependency_state_signature(right)


def load_contract(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1", "accepted_snapshots": []}
    return read_json(path)


def accepted_epoch(contract: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any] | None:
    for entry in contract.get("accepted_snapshots", []):
        if snapshots_equivalent(entry.get("snapshot") or {}, snapshot):
            return entry
    return None


def diff_snapshots(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    diffs = []
    prev_pixi = previous.get("pixi") or {}
    curr_pixi = current.get("pixi") or {}
    prev_lock = (prev_pixi.get("lockfile") or {}).get("sha256")
    curr_lock = (curr_pixi.get("lockfile") or {}).get("sha256")
    if prev_lock != curr_lock:
        diffs.append(f"pixi.lock sha256 changed: {prev_lock} -> {curr_lock}")
    if prev_pixi.get("environment") != curr_pixi.get("environment"):
        diffs.append(f"Pixi environment changed: {prev_pixi.get('environment')} -> {curr_pixi.get('environment')}")
    prev_packages = previous.get("packages") or {}
    curr_packages = current.get("packages") or {}
    for name in sorted(set(prev_packages) | set(curr_packages)):
        if prev_packages.get(name) != curr_packages.get(name):
            diffs.append(f"package {name}: {prev_packages.get(name)} -> {curr_packages.get(name)}")
    prev_runtime = {package_record_name(record): record for record in previous.get("runtime_packages") or []}
    curr_runtime = {package_record_name(record): record for record in current.get("runtime_packages") or []}
    for name in sorted(set(prev_runtime) | set(curr_runtime)):
        prev_record = prev_runtime.get(name)
        curr_record = curr_runtime.get(name)
        if prev_record is None:
            diffs.append(f"package {name} source added")
            continue
        if curr_record is None:
            diffs.append(f"package {name} source removed")
            continue
        prev_commit = package_source_commit(prev_record)
        curr_commit = package_source_commit(curr_record)
        if prev_commit != curr_commit:
            diffs.append(f"package {name} source commit changed: {prev_commit} -> {curr_commit}")
            continue
        prev_url = package_source_url(prev_record)
        curr_url = package_source_url(curr_record)
        if prev_url != curr_url:
            diffs.append(f"package {name} source URL changed: {prev_url} -> {curr_url}")
            continue
        if package_source_identity(prev_record) != package_source_identity(curr_record):
            diffs.append(f"package {name} source metadata changed")
    return diffs or ["dependency snapshot changed"]


def format_dependency_failure(contract: dict[str, Any], current_snapshot: dict[str, Any]) -> str:
    accepted = contract.get("accepted_snapshots") or []
    previous = accepted[-1].get("snapshot") if accepted else {}
    detail = "\n".join(f"- {item}" for item in diff_snapshots(previous, current_snapshot)) if previous else ""
    known = ", ".join(
        f"epoch {entry.get('epoch')} ({entry.get('snapshot', {}).get('digest', '')[:12]})" for entry in accepted
    )
    return (
        "Dependency runtime changed and is not an accepted epoch for this run.\n\n"
        f"Current snapshot: {current_snapshot.get('digest')}\n"
        f"Accepted epochs: {known or 'none'}\n\n"
        f"{detail}\n\n"
        "Accept this dependency epoch knowingly with --acceptance-reason."
    )


def append_epoch(
    contract: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    reason: str,
    dry_run: bool,
    contract_path: Path,
) -> dict[str, Any]:
    accepted = contract.setdefault("accepted_snapshots", [])
    previous = accepted[-1].get("snapshot") if accepted else {}
    entry = {
        "epoch": len(accepted) + 1,
        "accepted_at": utc_now(),
        "reason": reason,
        "snapshot": snapshot,
        "diff_from_previous": diff_snapshots(previous, snapshot) if previous else [],
    }
    if not dry_run:
        accepted.append(entry)
        write_json(contract_path, contract)
    return contract


def check_dependency_contract(
    *,
    run_root: Path,
    project_root: Path,
    acceptance_reason: str | None = None,
    dry_run: bool = False,
    pixi_environment: str | None = None,
    snapshot: dict[str, Any] | None = None,
    package_names: tuple[str, ...] = ("reprotrail",),
) -> dict[str, Any]:
    """Check or accept the dependency epoch for a run root."""

    run_root = run_root.resolve()
    contract_path = run_root / CONTRACT_RELATIVE_PATH
    current = snapshot or build_dependency_snapshot(
        project_root=project_root,
        pixi_environment=pixi_environment,
        package_names=package_names,
    )
    contract = load_contract(contract_path)
    contract.setdefault("schema_version", "1")
    contract.setdefault("run_root", str(run_root))
    contract.setdefault("accepted_snapshots", [])

    existing = accepted_epoch(contract, current)
    if existing is not None:
        return {"status": "accepted", "epoch": existing, "snapshot": current}

    reason = str(acceptance_reason or "").strip()
    if not contract["accepted_snapshots"]:
        append_epoch(
            contract,
            current,
            reason=reason or "initial dependency epoch",
            dry_run=dry_run,
            contract_path=contract_path,
        )
        return {
            "status": "would_initialize" if dry_run else "initialized",
            "epoch": None,
            "snapshot": current,
        }
    if reason:
        append_epoch(
            contract,
            current,
            reason=reason,
            dry_run=dry_run,
            contract_path=contract_path,
        )
        return {
            "status": "would_accept" if dry_run else "accepted_new",
            "epoch": None,
            "snapshot": current,
        }
    raise RuntimeError(format_dependency_failure(contract, current))


def contract_epoch_for_snapshot(run_root: Path | None, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    if run_root is None:
        return None
    contract_path = run_root / CONTRACT_RELATIVE_PATH
    if not contract_path.exists():
        return None
    return accepted_epoch(load_contract(contract_path), snapshot)


def product_provenance_path_from_input(input_state: dict[str, Any]) -> Path | None:
    metadata = input_state.get("metadata") or {}
    product_provenance = metadata.get("product_provenance") or {}
    provenance_path = product_provenance.get("path")
    input_path = input_state.get("path")
    if not provenance_path or not input_path:
        return None
    path = Path(str(provenance_path))
    if path.is_absolute():
        return path
    return Path(str(input_path)).parent / path


def infer_snapshot_from_provenance(provenance: dict[str, Any]) -> dict[str, Any] | None:
    return provenance.get("dependency_snapshot")


def product_readme_path(provenance_path: Path, record: dict[str, Any]) -> Path | None:
    product = record.get("product") or {}
    if not product.get("data"):
        return None
    return provenance_path.parent / str(product.get("readme_file") or "README.md")


def runtime_note(consistency: dict[str, Any]) -> str:
    different = [item for item in consistency.get("input_products", []) if item.get("comparison") == "different"]
    if not different:
        return ""
    lines = [
        README_NOTE_START,
        "",
        "## Runtime environment note",
        "",
        "This product was produced under a different dependency epoch than one "
        "or more input products. This is an audit fact, not automatically an error.",
        "",
        f"- Output dependency snapshot: `{consistency.get('output_snapshot_digest')}`",
    ]
    if consistency.get("output_epoch"):
        lines.append(f"- Output dependency epoch: `{consistency['output_epoch']}`")
    lines.append("- Input products with different runtime snapshots:")
    for item in different:
        label = item.get("path") or item.get("provenance") or "unknown input"
        digest = item.get("snapshot_digest") or "unknown"
        lines.append(f"  - `{label}` (`{digest}`)")
    lines.extend(["", README_NOTE_END, ""])
    return "\n".join(lines)


def append_readme_note(readme_path: Path | None, note: str) -> None:
    if not readme_path or not note or not readme_path.exists():
        return
    text = readme_path.read_text(encoding="utf-8")
    if README_NOTE_START in text and README_NOTE_END in text:
        start = text.index(README_NOTE_START)
        end = text.index(README_NOTE_END, start) + len(README_NOTE_END)
        text = text[:start].rstrip() + "\n\n" + note.strip() + text[end:]
    else:
        text = text.rstrip() + "\n\n" + note.strip() + "\n"
    readme_path.write_text(text, encoding="utf-8")


def annotate_product_environment_consistency(
    provenance_path: Path | None,
    *,
    run_root: Path | None,
    output_snapshot: dict[str, Any],
    output_epoch: dict[str, Any] | None,
    append_readme: bool = False,
) -> dict[str, Any] | None:
    """Annotate a product with input/output dependency-epoch consistency."""

    if provenance_path is None or not provenance_path.exists():
        return None
    record = read_json(provenance_path)
    if not record.get("product"):
        return None

    input_products = []
    for input_state in record.get("input_paths") or []:
        input_prov_path = product_provenance_path_from_input(input_state)
        if input_prov_path is None or not input_prov_path.exists():
            continue
        input_record = read_json(input_prov_path)
        input_snapshot = infer_snapshot_from_provenance(input_record)
        if input_snapshot is None:
            comparison = "unknown"
        elif snapshots_equivalent(output_snapshot, input_snapshot):
            comparison = "same"
        else:
            comparison = "different"
        input_epoch = contract_epoch_for_snapshot(run_root, input_snapshot) if input_snapshot and run_root else None
        input_products.append(
            {
                "path": input_state.get("path"),
                "provenance": str(input_prov_path),
                "snapshot_digest": input_snapshot.get("digest") if input_snapshot else None,
                "epoch": input_epoch.get("epoch") if input_epoch else None,
                "comparison": comparison,
            }
        )
    consistency = {
        "schema_version": "1",
        "output_snapshot_digest": output_snapshot.get("digest"),
        "output_epoch": output_epoch.get("epoch") if output_epoch else None,
        "input_products": input_products,
        "mixed_input_environments": any(item.get("comparison") == "different" for item in input_products),
        "unknown_input_environments": sum(1 for item in input_products if item.get("comparison") == "unknown"),
    }
    record["dependency_snapshot"] = output_snapshot
    if output_epoch:
        record["dependency_epoch"] = {
            "epoch": output_epoch.get("epoch"),
            "accepted_at": output_epoch.get("accepted_at"),
            "reason": output_epoch.get("reason"),
        }
    record["environment_consistency"] = consistency
    write_json(provenance_path, record)
    if append_readme:
        append_readme_note(product_readme_path(provenance_path, record), runtime_note(consistency))
    return consistency


def product_provenance_files(run_root: Path, markers: tuple[str, ...] = ()) -> list[Path]:
    roots = markers or ("products",)
    files = []
    for marker in roots:
        root = run_root / marker
        if root.exists():
            files.extend(sorted(root.rglob("*.prov.json")))
    if not files:
        files.extend(sorted(run_root.rglob("*.prov.json")))
    return sorted(set(files))


def audit_dependency_epochs(
    *,
    run_root: Path,
    output: Path,
    product_provenance: list[Path] | None = None,
    product_root_markers: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Scan product provenance files and summarize accepted dependency epochs."""

    run_root = run_root.resolve()
    contract = load_contract(run_root / CONTRACT_RELATIVE_PATH)
    accepted = contract.get("accepted_snapshots") or []
    paths = product_provenance or product_provenance_files(run_root, product_root_markers)
    products = []
    epoch_counts: dict[str, int] = {}
    unknown = 0
    for path in paths:
        if not path.exists():
            continue
        record = read_json(path)
        if not record.get("product"):
            continue
        snapshot = infer_snapshot_from_provenance(record)
        epoch = accepted_epoch(contract, snapshot) if snapshot else None
        if epoch:
            epoch_key = str(epoch.get("epoch"))
            epoch_counts[epoch_key] = epoch_counts.get(epoch_key, 0) + 1
        else:
            unknown += 1
        products.append(
            {
                "provenance": os.path.relpath(path, run_root),
                "product": record.get("product"),
                "snapshot_digest": snapshot.get("digest") if snapshot else None,
                "epoch": epoch.get("epoch") if epoch else None,
                "environment_consistency": record.get("environment_consistency"),
            }
        )
    payload = {
        "schema_version": "1",
        "run_root": str(run_root),
        "contract": os.path.relpath(run_root / CONTRACT_RELATIVE_PATH, run_root),
        "accepted_epoch_count": len(accepted),
        "product_count": len(products),
        "epoch_counts": epoch_counts,
        "mixed_accepted_epochs": len(epoch_counts) > 1,
        "unknown_or_legacy_product_count": unknown,
        "products": products,
        "status": "report_only",
        "generated_at": utc_now(),
    }
    write_json(output, payload)
    return payload
