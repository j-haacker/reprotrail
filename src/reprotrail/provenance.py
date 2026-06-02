"""Capture portable provenance metadata for data-processing outputs."""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ._json import to_jsonable

Backend = Literal["git-lfs", "dvc", "git", "filesystem", "unknown"]


@dataclass(frozen=True)
class GitState:
    """Snapshot of a Git repository at one point in time."""

    repo_root: Path
    commit: str | None
    branch: str | None
    remote_url: str | None
    dirty: bool
    dirty_marker: str
    status_short: str
    diff_hash: str | None = None


@dataclass(frozen=True)
class InputPathState:
    """Snapshot of an input path and metadata that identifies it."""

    path: Path
    exists: bool
    kind: str
    backend: Backend
    metadata: dict[str, Any]
    git_state: GitState | None = None
    git_path: str | None = None
    git_status: str = ""
    error: str | None = None


def canonicalize_remote_url(remote_url: str | None) -> str | None:
    """Return a portable, reader-friendly form of a Git remote URL."""

    if not remote_url:
        return None
    remote_url = remote_url.strip()
    if not remote_url:
        return None

    def clean_path(path: str) -> str:
        path = path.strip("/")
        return path[:-4] if path.endswith(".git") else path

    if remote_url.startswith(("http://", "https://")):
        scheme, rest = remote_url.split("://", 1)
        return f"{scheme}://{clean_path(rest)}"
    match = re.match(r"git@([^:]+):(.+)", remote_url)
    if match:
        host, path = match.groups()
        return f"https://{host}/{clean_path(path)}"
    match = re.match(r"ssh://(?:[^@/]+@)?([^/]+)/(.+)", remote_url)
    if match:
        host, path = match.groups()
        if host == "github":
            host = "github.com"
        return f"https://{host}/{clean_path(path)}"
    if remote_url.startswith("github:"):
        return f"https://github.com/{clean_path(remote_url.removeprefix('github:'))}"
    return remote_url


def run_git(args: Sequence[str], cwd: Path | str) -> tuple[bool, str, str]:
    """Run Git and return ``(ok, stdout, error)`` without raising."""

    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=Path(cwd),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as err:
        return False, "", str(err)
    if proc.returncode != 0:
        error = proc.stderr.strip() or proc.stdout.strip()
        return False, proc.stdout, error or f"git {' '.join(args)} failed"
    return True, proc.stdout, ""


def discover_repo_root(
    repo_dir: Path | str | None = None, *, max_parent_levels: int = 3
) -> Path | None:
    """Find the Git repository root for a directory or one of its parents."""

    start = Path.cwd() if repo_dir is None else Path(repo_dir).expanduser()
    candidates = [start.resolve()]
    current = candidates[0]
    for _ in range(max(0, max_parent_levels)):
        parent = current.parent
        if parent == current:
            break
        candidates.append(parent)
        current = parent
    for candidate in candidates:
        ok, output, _ = run_git(["rev-parse", "--show-toplevel"], cwd=candidate)
        if ok and output.strip():
            return Path(output.strip()).resolve()
    return None


def get_git_state(
    repo_dir: Path | str = ".",
    *,
    remote: str = "origin",
    include_diff_hash: bool = True,
) -> GitState:
    """Capture the current Git state for a repository."""

    repo_root = discover_repo_root(repo_dir, max_parent_levels=0)
    if repo_root is None:
        raise RuntimeError(f"Could not resolve git repository from {repo_dir}.")
    _, commit_output, _ = run_git(["rev-parse", "HEAD"], cwd=repo_root)
    _, branch_output, _ = run_git(["branch", "--show-current"], cwd=repo_root)
    remote_ok, remote_output, _ = run_git(["remote", "get-url", remote], cwd=repo_root)
    _, status_output, _ = run_git(["status", "--porcelain"], cwd=repo_root)

    dirty = bool(status_output.strip())
    diff_hash = None
    if include_diff_hash and dirty:
        _, staged, _ = run_git(
            ["diff", "--cached", "--binary", "--no-ext-diff", "--"], cwd=repo_root
        )
        _, unstaged, _ = run_git(
            ["diff", "--binary", "--no-ext-diff", "--"], cwd=repo_root
        )
        if staged or unstaged:
            diff_hash = hashlib.sha256(
                f"{staged}{unstaged}".encode("utf-8", errors="replace")
            ).hexdigest()

    return GitState(
        repo_root=repo_root,
        commit=commit_output.strip() or None,
        branch=branch_output.strip() or None,
        remote_url=canonicalize_remote_url(
            remote_output.strip() if remote_ok and remote_output.strip() else None
        ),
        dirty=dirty,
        dirty_marker="+dirty" if dirty else "",
        status_short=status_output.rstrip("\n"),
        diff_hash=diff_hash,
    )


def _repo_name_from_remote(remote_url: str | None) -> str | None:
    remote_url = canonicalize_remote_url(remote_url)
    if not remote_url:
        return None
    return remote_url.rstrip("/").removesuffix(".git").split("/")[-1] or None


def _repo_name_from_path(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).name or None


def public_git_state(state: GitState | Mapping[str, Any]) -> dict[str, Any]:
    """Return a portable Git state record suitable for public metadata."""

    data = to_jsonable(state)
    remote_url = canonicalize_remote_url(data.get("remote_url") or data.get("remote"))
    dirty = bool(data.get("dirty") or data.get("git_dirty"))
    name = (
        data.get("name")
        or data.get("repo")
        or data.get("package")
        or _repo_name_from_remote(remote_url)
        or _repo_name_from_path(data.get("repo_root"))
        or _repo_name_from_path(data.get("label"))
    )
    result: dict[str, Any] = {
        "name": name,
        "commit": data.get("commit") or data.get("git_head"),
        "branch": data.get("branch") or data.get("git_branch"),
        "remote_url": remote_url,
        "dirty": dirty,
    }
    if dirty:
        result["dirty_marker"] = data.get("dirty_marker") or "+dirty"
        for key in ("diff_hash", "status_hash", "status_short", "untracked_files"):
            if data.get(key):
                result[key] = data[key]
        if data.get("patch"):
            result["patch"] = data["patch"]
    return {key: value for key, value in result.items() if value not in (None, "")}


def format_git_state(state: GitState | Mapping[str, Any]) -> str:
    data = public_git_state(state)
    commit = str(data.get("commit") or "unknown")
    branch = data.get("branch") or "detached"
    prefix = f"{data['name']}@" if data.get("name") else ""
    marker = "+dirty" if data.get("dirty") else ""
    return f"{prefix}{commit[:12]}{marker} ({branch})"


def _path_kind(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def _repo_relative(path: Path, repo_root: Path) -> str | None:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except (OSError, ValueError):
        return None


def _git_status_for_path(repo_root: Path, rel: str) -> str:
    ok, output, _ = run_git(["status", "--porcelain", "--", rel], cwd=repo_root)
    return output.rstrip("\n") if ok else ""


def _is_git_tracked(repo_root: Path, rel: str) -> bool:
    ok, _, _ = run_git(["ls-files", "--error-unmatch", "--", rel], cwd=repo_root)
    return ok


def _parse_lfs_pointer(text: str) -> dict[str, Any] | None:
    lines = [line.strip() for line in text.splitlines()]
    if not lines or lines[0] != "version https://git-lfs.github.com/spec/v1":
        return None
    pointer: dict[str, Any] = {"is_pointer_file": True}
    for line in lines[1:]:
        if line.startswith("oid sha256:"):
            pointer["oid"] = line.removeprefix("oid sha256:")
        elif line.startswith("size "):
            raw = line.removeprefix("size ")
            try:
                pointer["size"] = int(raw)
            except ValueError:
                pointer["size"] = raw
    return pointer


def _lfs_metadata(path: Path, repo_root: Path | None, rel: str | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"is_pointer_file": False, "tracked_by_lfs": False}
    if path.is_file():
        try:
            pointer = _parse_lfs_pointer(
                path.read_text(encoding="utf-8", errors="replace")[:512]
            )
        except OSError as err:
            pointer = None
            metadata["pointer_error"] = str(err)
        if pointer is not None:
            metadata.update(pointer)
    if repo_root is None or rel is None:
        return metadata
    attr_ok, attr_output, _ = run_git(
        ["check-attr", "filter", "--", rel], cwd=repo_root
    )
    if attr_ok and attr_output.strip().endswith("filter: lfs"):
        metadata["tracked_by_lfs"] = True
    lfs_ok, lfs_output, lfs_error = run_git(
        ["lfs", "ls-files", "--long", "--", rel], cwd=repo_root
    )
    if lfs_ok and lfs_output.strip():
        parts = lfs_output.split()
        if parts:
            metadata["oid"] = parts[0]
            metadata["tracked_by_lfs"] = True
        metadata["lfs_ls_files"] = lfs_output.strip()
    elif lfs_error:
        metadata["lfs_error"] = lfs_error
    return metadata


def _simple_dvc_outputs(text: str) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("-"):
            if current:
                outputs.append(current)
            current = {}
            line = line[1:].strip()
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"\'')
        if key in {"path", "md5", "hash", "etag", "size", "nfiles"}:
            current[key] = value
    if current:
        outputs.append(current)
    return outputs


def _dvc_metadata(path: Path, repo_root: Path | None, rel: str | None) -> dict[str, Any]:
    candidates: list[Path] = []
    if repo_root is not None and rel is not None:
        candidates.append(repo_root / f"{rel}.dvc")
        candidates.append(repo_root / "dvc.lock")
    candidates.append(path.with_name(f"{path.name}.dvc"))

    metadata: dict[str, Any] = {"dvc_files": [], "outputs": []}
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen or not candidate.exists() or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            metadata.setdefault("errors", []).append(
                {"path": str(candidate), "error": str(err)}
            )
            continue
        if candidate.name == "dvc.lock" and rel is not None and rel not in text:
            continue
        dvc_info = {"path": str(candidate), "outputs": _simple_dvc_outputs(text)}
        if repo_root is not None:
            dvc_rel = _repo_relative(candidate, repo_root)
            if dvc_rel is not None:
                dvc_info["git_status"] = _git_status_for_path(repo_root, dvc_rel)
        metadata["dvc_files"].append(dvc_info)
        metadata["outputs"].extend(dvc_info["outputs"])
    return metadata


def summarize_directory(path: Path | str, *, max_entries: int = 20_000) -> dict[str, Any]:
    """Summarize a directory without embedding a full file listing."""

    root = Path(path)
    file_count = 0
    total_bytes = 0
    digest = hashlib.sha256()
    truncated = False
    for child in sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda p: p.as_posix(),
    ):
        file_count += 1
        try:
            stat = child.stat()
        except OSError:
            continue
        total_bytes += stat.st_size
        if file_count <= max_entries:
            rel = child.relative_to(root).as_posix()
            digest.update(f"{rel}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
        else:
            truncated = True
    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "manifest_hash": digest.hexdigest(),
        "manifest_hash_kind": "paths-size-mtime-ns",
        "manifest_truncated": truncated,
        "max_entries": max_entries,
    }


def get_input_path_state(path: Path | str) -> InputPathState:
    """Inspect one input path and classify its provenance backend."""

    target = Path(path).expanduser().resolve()
    kind = _path_kind(target)
    repo_root = discover_repo_root(
        target if target.exists() else target.parent, max_parent_levels=8
    )
    git_state = None
    rel = None
    git_status = ""
    metadata: dict[str, Any] = {}
    error = None
    if repo_root is not None:
        rel = _repo_relative(target, repo_root)
        if rel is not None:
            try:
                git_state = get_git_state(repo_root)
                git_status = _git_status_for_path(repo_root, rel)
            except RuntimeError as err:
                error = str(err)
    if kind == "directory":
        metadata["directory"] = summarize_directory(target)
    dvc = _dvc_metadata(target, repo_root, rel)
    lfs = _lfs_metadata(target, repo_root, rel)
    metadata.update({"lfs": lfs, "dvc": dvc})
    if dvc["dvc_files"]:
        backend: Backend = "dvc"
    elif lfs.get("tracked_by_lfs") or lfs.get("is_pointer_file"):
        backend = "git-lfs"
    elif repo_root is not None and rel is not None and _is_git_tracked(repo_root, rel):
        backend = "git"
    elif kind != "missing":
        backend = "filesystem"
    else:
        backend = "unknown"
    return InputPathState(
        path=target,
        exists=target.exists(),
        kind=kind,
        backend=backend,
        metadata=metadata,
        git_state=git_state,
        git_path=rel,
        git_status=git_status,
        error=error,
    )


def get_input_path_states(paths: Iterable[Path | str]) -> list[InputPathState]:
    """Inspect multiple input paths, preserving input order."""

    return [get_input_path_state(path) for path in paths]


def public_input_path_state(state: InputPathState | Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact input path record without local-only repository roots."""

    data = to_jsonable(state)
    metadata = data.get("metadata", {})
    public_metadata: dict[str, Any] = {}
    for key in ("directory", "selection", "product_provenance"):
        if metadata.get(key):
            public_metadata[key] = metadata[key]
    lfs = metadata.get("lfs") or {}
    if lfs.get("tracked_by_lfs") or lfs.get("is_pointer_file"):
        public_metadata["lfs"] = {
            key: value
            for key, value in lfs.items()
            if key in {"tracked_by_lfs", "is_pointer_file", "oid", "size"}
        }
    dvc = metadata.get("dvc") or {}
    if dvc.get("dvc_files") or dvc.get("outputs"):
        public_metadata["dvc"] = dvc

    result: dict[str, Any] = {
        "path": data.get("git_path") or data.get("path"),
        "exists": data.get("exists"),
        "kind": data.get("kind"),
        "backend": data.get("backend"),
    }
    if public_metadata:
        result["metadata"] = public_metadata
    if data.get("git_state"):
        result["git"] = public_git_state(data["git_state"])
    if data.get("git_status"):
        result["git_status"] = data["git_status"]
    if data.get("error"):
        result["error"] = data["error"]
    return {key: value for key, value in result.items() if value not in (None, "")}


def public_provenance(value: Any) -> Any:
    """Return provenance metadata intended to be written into public outputs."""

    if isinstance(value, InputPathState):
        return public_input_path_state(value)
    if isinstance(value, GitState):
        return public_git_state(value)
    data = to_jsonable(value)
    if isinstance(data, Mapping):
        result: dict[str, Any] = {}
        for key, item in data.items():
            if key in {"history_entry", "repo_root", "source_path"}:
                continue
            if key == "software_repos" and isinstance(item, Iterable):
                result[key] = [public_git_state(state) for state in item]
                continue
            if key == "input_paths" and isinstance(item, Iterable):
                result[key] = [public_input_path_state(state) for state in item]
                continue
            if key == "remote_url":
                result[key] = canonicalize_remote_url(str(item)) if item else None
                continue
            if key == "diff_hash" and not data.get("dirty"):
                continue
            if key == "status_short" and not item:
                continue
            result[str(key)] = public_provenance(item)
        return {key: item for key, item in result.items() if item not in (None, "")}
    if isinstance(data, list):
        return [public_provenance(item) for item in data]
    return data


def clean_command_parts(parts: Sequence[str]) -> list[str]:
    """Remove reprotrail/provenance sidecar flags from recorded commands."""

    cleaned = []
    skip_next = False
    for part in [str(item) for item in parts]:
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


def _command_text(command: str | Sequence[str] | None) -> str:
    if command is None:
        return "unknown command"
    if isinstance(command, str):
        return command
    return shlex.join(clean_command_parts(command))


def build_cf_history_entry(
    command: str | Sequence[str] | None = None,
    *,
    git_state: GitState | Mapping[str, Any] | None = None,
    git_states: Sequence[GitState | Mapping[str, Any]] = (),
    input_states: Sequence[InputPathState | Mapping[str, Any]] = (),
    timestamp: datetime | None = None,
    include_inputs: bool = False,
) -> str:
    """Build a timestamped history line suitable for CF/xarray attrs."""

    when = timestamp or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    parts = [when.astimezone(timezone.utc).isoformat(timespec="seconds")]
    parts.append(_command_text(command))
    states = list(git_states)
    if git_state is not None:
        states.insert(0, git_state)
    if states:
        parts.append("software=" + ", ".join(format_git_state(state) for state in states))
    if include_inputs and input_states:
        compact = []
        for state in input_states:
            data = to_jsonable(state)
            compact.append(
                f"{Path(data.get('path', 'unknown')).name}:{data.get('backend', 'unknown')}"
            )
        parts.append("inputs=" + ", ".join(compact))
    return "; ".join(parts)


def append_cf_history(existing: str | None, entry: str) -> str:
    """Prepend a new entry to existing CF history text."""

    existing = (existing or "").strip()
    return entry if not existing else f"{entry}\n{existing}"


def append_xarray_history(obj: Any, entry: str, *, copy: bool = False) -> Any:
    """Prepend a history entry to an xarray-like object's ``attrs``."""

    if copy:
        obj = obj.copy()
    obj.attrs["history"] = append_cf_history(obj.attrs.get("history"), entry)
    return obj


def enforce_clean_repos(
    repos: Iterable[Path | str],
    *,
    allow_dirty: bool = False,
    missing_ok: bool = True,
) -> list[GitState]:
    """Validate that repositories are clean unless dirty state is allowed."""

    states: list[GitState] = []
    failures = []
    for repo in repos:
        repo_path = Path(repo).expanduser()
        if not repo_path.exists():
            if not missing_ok:
                failures.append(f"{repo}: path does not exist")
            continue
        try:
            state = get_git_state(repo_path)
        except RuntimeError as err:
            if not missing_ok:
                failures.append(str(err))
            continue
        states.append(state)
        if state.dirty and not allow_dirty:
            failures.append(f"{state.repo_root} is dirty:\n{state.status_short}")
    if failures:
        raise RuntimeError(
            "Dirty software repository state requires --allow-dirty.\n"
            + "\n\n".join(failures)
        )
    return states


def env_allows_dirty(var: str = "REPROTRAIL_ALLOW_DIRTY") -> bool:
    """Return whether an environment variable opts into dirty repositories."""

    return os.environ.get(var, "").strip().lower() in {"1", "true", "yes", "on"}
