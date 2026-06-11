"""Pixi environment and editable dependency helpers."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ._json import write_json
from ._paths import relative_path, sha256_file
from .provenance import canonicalize_remote_url


class PixiGitFreshnessError(RuntimeError):
    """Raised when Pixi Git freshness cannot be checked safely."""


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

    return value in {".", "./"} or value.startswith(("./", "../", "/", "~"))


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


def pixi_dependency_records(lock_text: str, environment: str | None, project_root: Path) -> list[dict[str, Any]]:
    """Classify local Pixi dependencies as project-self or external-editable."""

    package_names = pixi_package_names_by_pypi(lock_text)
    records: list[dict[str, Any]] = []
    for value in pixi_local_path_dependencies(lock_text, environment):
        resolved = resolve_pixi_path(value, project_root)
        kind = "project-self" if is_project_self_dependency(value, resolved, project_root) else "external-editable"
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
        {key: value for key, value in record.items() if not key.startswith("_") and value not in (None, "")}
        for record in records
    ]


def editable_dependency_failures(dependency_records: list[dict[str, Any]], *, allow_editable: bool) -> list[str]:
    """Return policy failures for external editable/path dependencies."""

    failures = []
    for record in dependency_records:
        if record.get("kind") != "external-editable":
            continue
        path = record.get("path")
        resolved = record.get("_resolved_path")
        if not allow_editable:
            failures.append(f"External editable/path dependency requires --allow-editable: {path} ({resolved})")
            continue
        if not record.get("_repo_root"):
            failures.append(f"Editable/path dependency is not a resolvable Git repository: {path} ({resolved})")
    return failures


def repo_paths_with_dependencies(repos: list[str], dependency_records: list[dict[str, Any]]) -> list[str]:
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


def normalize_package_name(name: str) -> str:
    """Return a normalized Python distribution name."""

    return re.sub(r"[-_.]+", "-", name).lower()


def _distribution_name(distribution: importlib.metadata.Distribution, requested_name: str) -> str:
    metadata = getattr(distribution, "metadata", {}) or {}
    try:
        return str(metadata.get("Name") or requested_name)
    except AttributeError:
        return requested_name


def _safe_direct_url(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result: dict[str, Any] = {}
    vcs_info = payload.get("vcs_info") if isinstance(payload.get("vcs_info"), dict) else {}
    url = payload.get("url")
    if isinstance(url, str) and url:
        parsed = urlparse(url)
        if parsed.scheme == "file" or (not parsed.scheme and is_local_pixi_ref(url)):
            result["url"] = "<redacted-local-path>"
            result["url_kind"] = "file"
            result["path_redacted"] = True
        elif vcs_info.get("vcs") == "git":
            result["url"] = canonicalize_remote_url(url)
        else:
            result["url"] = url
    dir_info = payload.get("dir_info")
    if isinstance(dir_info, dict) and "editable" in dir_info:
        result["dir_info"] = {"editable": bool(dir_info["editable"])}
    if vcs_info:
        public_vcs = {
            key: vcs_info[key]
            for key in ("vcs", "commit_id", "requested_revision")
            if vcs_info.get(key) not in (None, "")
        }
        if public_vcs:
            result["vcs_info"] = public_vcs
    return result


def package_records(package_names: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
    """Return installed package records with sanitized source identity."""

    records = []
    seen: set[str] = set()
    for requested_name in package_names:
        seen_key = normalize_package_name(requested_name)
        if seen_key in seen:
            continue
        seen.add(seen_key)
        try:
            distribution = importlib.metadata.distribution(requested_name)
        except importlib.metadata.PackageNotFoundError:
            continue
        installed_name = _distribution_name(distribution, requested_name)
        record: dict[str, Any] = {
            "requested_name": requested_name,
            "name": normalize_package_name(installed_name),
            "version": distribution.version,
        }
        try:
            direct_url_text = distribution.read_text("direct_url.json")
        except OSError:
            direct_url_text = None
        if direct_url_text:
            try:
                direct_url = _safe_direct_url(json.loads(direct_url_text))
            except json.JSONDecodeError:
                direct_url = {}
                record["direct_url_error"] = "invalid-json"
            if direct_url:
                record["direct_url"] = direct_url
        records.append(record)
    return records


def package_versions(package_names: list[str] | tuple[str, ...]) -> dict[str, str]:
    """Return installed versions for package names that can be resolved."""

    versions = {}
    for name in package_names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def pixi_package_license_records(project_root: Path, pixi_environment: str | None) -> list[dict[str, Any]]:
    """Return package license metadata from the local Pixi environment."""

    command = [
        "pixi",
        "list",
        "--json",
        "--no-install",
        "--manifest-path",
        str(project_root),
    ]
    if pixi_environment:
        command.extend(["--environment", pixi_environment])
    try:
        proc = subprocess.run(
            command,
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as err:
        raise RuntimeError(f"pixi package license discovery failed: {err}") from err
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "pixi list failed"
        raise RuntimeError(message)
    data = json.loads(proc.stdout or "[]")
    if not isinstance(data, list):
        raise RuntimeError("pixi list returned an unexpected JSON payload.")
    return [
        {
            "name": item.get("name"),
            "version": item.get("version"),
            "kind": item.get("kind"),
            "license": item.get("license"),
            "license_family": item.get("license_family"),
        }
        for item in data
        if isinstance(item, dict)
    ]


def _first_string_by_key(payload: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(payload, dict):
        lowered = {str(key).lower(): value for key, value in payload.items()}
        for key in keys:
            value = lowered.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _first_string_by_key(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _first_string_by_key(value, keys)
            if found:
                return found
    return None


def _contains_git_marker(payload: Any) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered_key = str(key).lower()
            if lowered_key == "vcs" and isinstance(value, str) and value.lower() == "git":
                return True
            if lowered_key in {"git", "vcs_info"} and value not in (None, "", {}):
                return True
            if _contains_git_marker(value):
                return True
    elif isinstance(payload, list):
        return any(_contains_git_marker(value) for value in payload)
    elif isinstance(payload, str):
        return _looks_like_git_url(payload)
    return False


def _looks_like_git_url(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        lowered.startswith(("git+", "git@", "github:"))
        or ".git" in lowered
        or "://github.com/" in lowered
        or "://github/" in lowered
    )


def _split_git_url_revision(value: str) -> tuple[str | None, str | None]:
    candidate = value.strip()
    if not candidate:
        return None, None
    if " @ git+" in candidate:
        candidate = candidate.split(" @ ", 1)[1]
    had_git_prefix = candidate.startswith("git+")
    if candidate.startswith("git+"):
        candidate = candidate.removeprefix("git+")
    candidate, _, fragment = candidate.partition("#")
    if not _looks_like_git_url(candidate):
        return None, None
    revision = None
    git_revision_marker = ".git@"
    if git_revision_marker in candidate:
        candidate, revision = candidate.rsplit("@", 1)
    elif had_git_prefix:
        last_at = candidate.rfind("@")
        if last_at > candidate.rfind("/"):
            candidate, revision = candidate[:last_at], candidate[last_at + 1 :]
    if not revision and fragment:
        for item in fragment.split("&"):
            key, _, fragment_value = item.partition("=")
            if key in {"commit", "rev", "revision", "tag", "branch"} and fragment_value:
                revision = fragment_value
                break
    return canonicalize_remote_url(candidate), revision or None


def _first_git_url(payload: Any) -> tuple[str | None, str | None]:
    for key in ("url", "git", "pypi", "source", "source_url"):
        value = _first_string_by_key(payload, (key,))
        if not value:
            continue
        url, revision = _split_git_url_revision(value)
        if url:
            return url, revision
    return None, None


def _pixi_git_source(payload: Any) -> tuple[dict[str, str] | None, bool]:
    looks_git = _contains_git_marker(payload)
    if not looks_git:
        return None, False

    url, url_revision = _first_git_url(payload)
    commit = _first_string_by_key(payload, ("commit_id", "commit", "resolved_commit"))
    requested_revision = _first_string_by_key(
        payload,
        ("requested_revision", "requested_ref", "rev", "branch", "tag"),
    )
    requested_revision = requested_revision or url_revision

    source = {
        "kind": "git",
        "url": url,
        "commit": commit,
        "requested_revision": requested_revision,
    }
    public_source = {key: value for key, value in source.items() if value not in (None, "")}
    if "commit" not in public_source and "requested_revision" not in public_source:
        return None, True
    return public_source, True


def _package_change_name(row: dict[str, Any]) -> str | None:
    value = row.get("name")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _pixi_update_change_rows(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    environment = payload.get("environment")
    if not isinstance(environment, dict):
        raise PixiGitFreshnessError("pixi update returned an unexpected JSON payload.")

    rows: list[tuple[str, dict[str, Any]]] = []
    for platforms in environment.values():
        if not isinstance(platforms, dict):
            raise PixiGitFreshnessError("pixi update returned an unexpected JSON payload.")
        for platform_name, changes in platforms.items():
            if not isinstance(changes, list):
                raise PixiGitFreshnessError("pixi update returned an unexpected JSON payload.")
            for change in changes:
                if isinstance(change, dict):
                    rows.append((str(platform_name), change))
                else:
                    raise PixiGitFreshnessError("pixi update returned an unexpected JSON payload.")
    return rows


def _pixi_git_freshness_report(
    payload: dict[str, Any],
    *,
    environment: str,
    packages: tuple[str, ...],
) -> dict[str, Any]:
    selected = {normalize_package_name(package) for package in packages}
    stale_packages: list[dict[str, Any]] = []

    for platform_name, row in _pixi_update_change_rows(payload):
        name = _package_change_name(row)
        if not name or normalize_package_name(name) not in selected:
            continue

        before_source, before_looks_git = _pixi_git_source(row.get("before"))
        after_source, after_looks_git = _pixi_git_source(row.get("after"))
        if not before_looks_git and not after_looks_git:
            continue
        if before_source is None or after_source is None:
            raise PixiGitFreshnessError(
                f"pixi update reported a Git-backed change for {name}, but reprotrail could not "
                "extract a commit or requested revision."
            )
        if before_source == after_source:
            continue
        stale_packages.append(
            {
                "name": name,
                "status": "changed",
                "platform": platform_name,
                "from": before_source,
                "to": after_source,
            }
        )

    return {
        "status": "stale" if stale_packages else "fresh",
        "environment": environment,
        "checked_packages": list(packages),
        "packages": stale_packages,
    }


def check_pixi_git_freshness(
    project_root: Path,
    environment: str,
    packages: tuple[str, ...],
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Check whether selected Git-backed Pixi packages would move on update."""

    if not environment:
        raise PixiGitFreshnessError("A Pixi environment is required.")
    if not packages:
        raise PixiGitFreshnessError("At least one package is required.")

    project_root = Path(project_root)
    manifest_path = Path(manifest_path) if manifest_path is not None else project_root
    command = [
        "pixi",
        "update",
        "--dry-run",
        "--json",
        "--manifest-path",
        str(manifest_path),
        "-e",
        environment,
        *packages,
    ]
    try:
        proc = subprocess.run(
            command,
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as err:
        raise PixiGitFreshnessError(f"pixi update dry-run failed: {err}") from err
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or f"pixi exited with code {proc.returncode}"
        raise PixiGitFreshnessError(f"pixi update dry-run failed: {message}")
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as err:
        raise PixiGitFreshnessError(f"pixi update returned invalid JSON: {err.msg}") from err
    if not isinstance(payload, dict):
        raise PixiGitFreshnessError("pixi update returned an unexpected JSON payload.")
    return _pixi_git_freshness_report(payload, environment=environment, packages=packages)


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
    external_dependencies = [record for record in public_records if record.get("kind") == "external-editable"]
    return {
        "schema_version": "1",
        "manager": "pixi",
        "pixi": {
            "environment": pixi_environment,
            "allow_editable": allow_editable,
            "editable_dependencies": bool(external_dependencies),
            "local_path_dependencies": [str(record["path"]) for record in public_records if record.get("path")],
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
        "runtime_packages": package_records(package_names),
        "env_vars": {key: os.environ[key] for key in env_var_whitelist if key in os.environ},
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
