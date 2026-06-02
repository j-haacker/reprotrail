"""Pixi environment and editable dependency helpers."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ._json import write_json
from ._paths import relative_path, sha256_file


def pixi_environment_block(lock_text: str, environment: str | None) -> str:
    """Return the environment block from a Pixi lockfile."""

    if not environment:
        return ""
    lines = lock_text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line == f"  {environment}:":
            start = index
            break
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            end = index
            break
    return "\n".join(lines[start:end])


def is_local_pixi_ref(value: str) -> bool:
    """Return whether a Pixi pypi reference points at a local path."""

    return (
        value in {".", "./"}
        or value.startswith(("./", "../", "/", "~"))
    )


def pixi_local_path_dependencies(lock_text: str, environment: str | None) -> list[str]:
    """List local path dependencies in one Pixi environment."""

    block = pixi_environment_block(lock_text, environment)
    paths = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- pypi: "):
            continue
        value = stripped.removeprefix("- pypi: ").strip().strip("'\"")
        if is_local_pixi_ref(value):
            paths.append(value)
    return sorted(set(paths))


def pixi_package_names_by_pypi(lock_text: str) -> dict[str, str]:
    """Return package names keyed by Pixi pypi source reference."""

    names: dict[str, str] = {}
    current: str | None = None
    for line in lock_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- pypi: "):
            current = stripped.removeprefix("- pypi: ").strip().strip("'\"")
            continue
        if current and stripped.startswith("name: "):
            names[current] = stripped.removeprefix("name: ").strip().strip("'\"")
            current = None
        elif stripped.startswith("- "):
            current = None
    return names


def resolve_pixi_path(value: str, project_root: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (project_root / path).resolve()


def is_project_self_dependency(value: str, resolved: Path, project_root: Path) -> bool:
    normalized = value.rstrip("/") or "."
    if normalized in {".", "./"}:
        return True
    try:
        return resolved == project_root.resolve()
    except OSError:
        return False


def git_value(repo: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout.rstrip("\n") if proc.returncode == 0 else ""


def git_repo_root(path: Path) -> Path | None:
    if not path.exists():
        return None
    root = git_value(path, ["rev-parse", "--show-toplevel"])
    return Path(root).resolve() if root else None


def pixi_dependency_records(
    lock_text: str, environment: str | None, project_root: Path
) -> list[dict[str, Any]]:
    """Classify local Pixi dependencies as project-self or external-editable."""

    package_names = pixi_package_names_by_pypi(lock_text)
    records: list[dict[str, Any]] = []
    for value in pixi_local_path_dependencies(lock_text, environment):
        resolved = resolve_pixi_path(value, project_root)
        kind = (
            "project-self"
            if is_project_self_dependency(value, resolved, project_root)
            else "external-editable"
        )
        repo_root = git_repo_root(resolved)
        package = package_names.get(value)
        repo_name = repo_root.name if repo_root is not None else Path(value).name
        records.append(
            {
                "path": value,
                "package": package,
                "repo": repo_name or package,
                "kind": kind,
                "local": True,
                "editable": True,
                "_resolved_path": str(resolved),
                "_repo_root": str(repo_root) if repo_root is not None else None,
            }
        )
    return records


def public_dependency_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove private resolved-path fields from dependency records."""

    return [
        {
            key: value
            for key, value in record.items()
            if not key.startswith("_") and value not in (None, "")
        }
        for record in records
    ]


def editable_dependency_failures(
    dependency_records: list[dict[str, Any]], *, allow_editable: bool
) -> list[str]:
    """Return policy failures for external editable/path dependencies."""

    failures = []
    for record in dependency_records:
        if record.get("kind") != "external-editable":
            continue
        path = record.get("path")
        resolved = record.get("_resolved_path")
        if not allow_editable:
            failures.append(
                "External editable/path dependency requires --allow-editable: "
                f"{path} ({resolved})"
            )
            continue
        if not record.get("_repo_root"):
            failures.append(
                "Editable/path dependency is not a resolvable Git repository: "
                f"{path} ({resolved})"
            )
    return failures


def repo_paths_with_dependencies(
    repos: list[str], dependency_records: list[dict[str, Any]]
) -> list[str]:
    """Append resolved editable dependency Git repos to an inspected repo list."""

    result = list(repos)
    seen: set[Path] = set()
    for repo in result:
        try:
            seen.add(Path(repo).resolve())
        except OSError:
            continue
    for record in dependency_records:
        repo_root = record.get("_repo_root")
        if record.get("kind") != "external-editable" or not repo_root:
            continue
        repo_path = Path(str(repo_root)).resolve()
        if repo_path in seen:
            continue
        seen.add(repo_path)
        result.append(str(repo_path))
    return result


def package_versions(package_names: list[str] | tuple[str, ...]) -> dict[str, str]:
    """Return installed versions for package names that can be resolved."""

    versions = {}
    for name in package_names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def infer_pixi_environment(project_root: Path, value: str | None = None) -> str | None:
    """Infer the active Pixi environment from an explicit value, env var, or Python."""

    if value:
        return value
    if os.environ.get("PIXI_ENVIRONMENT_NAME"):
        return os.environ["PIXI_ENVIRONMENT_NAME"]
    try:
        executable = Path(sys.executable).resolve()
        envs = (project_root / ".pixi" / "envs").resolve()
        relative = executable.relative_to(envs)
    except ValueError:
        return None
    return relative.parts[0] if relative.parts else None


def environment_summary(
    *,
    project_root: Path,
    pixi_environment: str | None,
    dependency_records: list[dict[str, Any]],
    allow_editable: bool,
    package_names: tuple[str, ...],
    env_var_whitelist: tuple[str, ...],
) -> dict[str, Any]:
    """Build a portable summary of the active runtime environment."""

    public_records = public_dependency_records(dependency_records)
    external_dependencies = [
        record for record in public_records if record.get("kind") == "external-editable"
    ]
    return {
        "schema_version": "1",
        "manager": "pixi",
        "pixi": {
            "environment": pixi_environment,
            "allow_editable": allow_editable,
            "editable_dependencies": bool(external_dependencies),
            "local_path_dependencies": [
                str(record["path"]) for record in public_records if record.get("path")
            ],
            "local_dependencies": public_records,
            "external_editable_dependencies": external_dependencies,
        },
        "python": {
            "version": sys.version,
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "platform": platform.platform(),
        },
        "packages": package_versions(package_names),
        "env_vars": {
            key: os.environ[key] for key in env_var_whitelist if key in os.environ
        },
        "project_root_name": project_root.name,
    }


def write_environment_bundle(
    *,
    run_root: Path,
    project_root: Path,
    lockfile: Path,
    pixi_environment: str | None,
    dependency_records: list[dict[str, Any]],
    allow_editable: bool,
    package_names: tuple[str, ...],
    env_var_whitelist: tuple[str, ...],
) -> dict[str, Any]:
    """Copy a Pixi lockfile and environment summary into provenance artifacts."""

    refs: dict[str, Any] = {}
    lock_hash = sha256_file(lockfile)
    env_dir = run_root / "provenance" / "environment" / lock_hash[:7]
    env_dir.mkdir(parents=True, exist_ok=True)
    lock_dest = env_dir / "pixi.lock"
    if not lock_dest.exists():
        shutil.copyfile(lockfile, lock_dest)
    if sha256_file(lock_dest) != lock_hash:
        raise RuntimeError("Stored provenance pixi.lock hash does not match original.")
    refs["lockfile"] = {
        "path": relative_path(lock_dest, run_root),
        "sha256": lock_hash,
    }
    summary = environment_summary(
        project_root=project_root,
        pixi_environment=pixi_environment,
        dependency_records=dependency_records,
        allow_editable=allow_editable,
        package_names=package_names,
        env_var_whitelist=env_var_whitelist,
    )
    summary_path = env_dir / "environment.json"
    write_json(summary_path, summary)
    refs["summary"] = {
        "path": relative_path(summary_path, run_root),
        "sha256": sha256_file(summary_path),
    }
    return refs
