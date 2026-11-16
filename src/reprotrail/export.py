"""Collect selected data and the recursive metadata needed to reproduce it."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

from ._json import read_json, write_json
from ._paths import sha256_file


class ExportError(RuntimeError):
    """Raised when a provenance graph cannot be collected safely."""


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode:
        raise ExportError(proc.stderr.strip() or f"git {args[0]} failed")
    return proc.stdout.strip()


def traced_repositories(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Return explicitly traced software, excluding input ownership metadata."""
    project = record.get("project_repo")
    return ([project] if project else []) + record.get("software_repos", []) + record.get("configured_repos", [])


def artifact_path(record: dict[str, Any], provenance: Path, ref: str) -> Path:
    path = Path(ref)
    if path.is_absolute():
        return path
    return (provenance.parent / (record.get("artifact_root") or {}).get("path", ".") / path).resolve()


def _root_for(path: Path) -> Path | None:
    try:
        return Path(_git("rev-parse", "--show-toplevel", cwd=path if path.is_dir() else path.parent))
    except (ExportError, OSError):
        return None


def resolve_input(item: dict[str, Any], provenance: Path, roots: dict[str, str]) -> Path:
    """Resolve repository-relative paths without silently choosing ambiguous roots."""
    path = Path(item["path"])
    if path.is_absolute():
        return path
    name = (item.get("git") or {}).get("name")
    if name in roots:
        return Path(roots[name]) / path
    candidates = {p.absolute() for p in [Path.cwd() / path, provenance.parent / path] if p.exists()}
    for parent in provenance.parents:
        if parent.name == name:
            return parent / path
    if len(candidates) > 1:
        raise ExportError(f"Ambiguous input {path}; provide --input-root {name or 'NAME'}=PATH")
    if candidates:
        return candidates.pop()
    # Retain an unresolved external requirement, rather than inventing a location.
    raise ExportError(f"Unresolved input {path}; provide --input-root {name or 'NAME'}=PATH")


def discover_products(
    provenances: list[str | Path],
    *,
    input_roots: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Inspect a dependency graph without copying data or creating files."""
    roots = {name: str(Path(path).resolve()) for name, path in (input_roots or {}).items()}
    if any(not re.fullmatch(r"[\w.-]+", name) or name in {".", ".."} for name in roots):
        raise ExportError("Input root names must be simple repository names.")
    selected = [Path(p).resolve() for p in provenances]
    if not selected:
        raise ExportError("At least one product provenance is required.")
    for p in selected:
        root = _root_for(p)
        if root is not None:
            roots.setdefault(root.name, str(root))
    graph: dict[str, Any] = {
        "schema_version": "1",
        "kind": "reprotrail-export",
        "roots": [],
        "input_roots": roots,
        "products": {},
        "files": {},
        "external_inputs": [],
        "repositories": [],
        "gaps": [],
    }
    paths: dict[Path, str] = {}
    destinations: dict[str, str] = {}

    def gap(owner: str, role: str, path: str, reason: str) -> None:
        graph["gaps"].append({"product": owner, "role": role, "path": path, "reason": reason})

    def collect(path: Path, owner: str, role: str, expected: str | None = None) -> None:
        path = path.resolve()
        if not path.is_file():
            gap(owner, role, str(path), "missing")
            return
        key = str(path)
        digest = graph["files"].get(key, {}).get("sha256") or sha256_file(path)
        if expected and digest != expected:
            gap(owner, role, str(path), "hash-mismatch")
        if key not in graph["files"]:
            location = None
            for name, root in sorted(roots.items(), key=lambda pair: -len(pair[1])):
                if path.is_relative_to(root):
                    location = f"metadata/{name}/{path.relative_to(root).as_posix()}"
                    break
            location = location or f"metadata/artifacts/{digest}/{path.name}"
            previous = destinations.setdefault(location, key)
            if previous != key:
                raise ExportError(f"Export destination collision: {location}")
            graph["files"][key] = {"path": location, "sha256": digest, "role": role}

    def visit(path: Path) -> str:
        path = path.resolve()
        if path in paths:
            return paths[path]
        try:
            record = read_json(path)
            if not isinstance(record, dict) or not isinstance(record.get("command"), list):
                raise ValueError("expected an object with a command list")
            if not all(isinstance(token, str) for token in record["command"]):
                raise ValueError("command must contain strings")
        except (OSError, ValueError) as exc:
            raise ExportError(f"Malformed provenance {path}: {exc}") from exc
        identity = sha256_file(path)
        paths[path] = identity
        if identity in graph["products"]:
            return identity
        product = record.get("product") or {}
        data_ref = product.get("data") or record.get("output_path")
        if not data_ref:
            raise ExportError(f"Provenance has no product data path: {path}")
        data = (path.parent / data_ref).resolve()
        node: dict[str, Any] = {
            "provenance": str(path),
            "sha256": identity,
            "data": str(data),
            "selected": path in selected,
            "dependencies": [],
            "inputs": [],
        }
        graph["products"][identity] = node
        collect(path, identity, "provenance")
        checksum = path.with_name(path.name + ".sha256")
        collect(checksum, identity, "provenance-checksum")
        if checksum.is_file():
            tokens = checksum.read_text().split()
            if not tokens or tokens[0] != identity:
                gap(identity, "provenance-checksum", str(checksum), "hash-mismatch")
        if node["selected"]:
            if not data.exists():
                gap(identity, "selected-data", str(data), "missing")
            for key, default in [
                ("readme_file", "README.md"),
                ("license_file", "LICENSE.md"),
                ("ro_crate_file", "ro-crate-metadata.json"),
            ]:
                sidecar = path.parent / product.get(key, default)
                if sidecar.is_file():
                    collect(sidecar, identity, "package-sidecar")
        for role, ref in (record.get("environment") or {}).items():
            if isinstance(ref, dict) and ref.get("path"):
                collect(artifact_path(record, path, ref["path"]), identity, role, ref.get("sha256"))
        for repo in traced_repositories(record):
            if repo not in graph["repositories"]:
                graph["repositories"].append(repo)
            patch = repo.get("patch") or {}
            if patch.get("path"):
                collect(artifact_path(record, path, patch["path"]), identity, "patch", patch.get("sha256"))
            elif repo.get("dirty"):
                gap(identity, "patch", str(repo.get("name", "repo")), "dirty repository without recorded patch")
            if repo.get("untracked_files"):
                gap(identity, "untracked-code", str(repo.get("name", "repo")), "untracked files are not archived")
        for item in record.get("input_paths") or []:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ExportError(f"Malformed input in {path}")
            pp = (item.get("metadata") or {}).get("product_provenance") or {}
            entry: dict[str, Any] = {"recorded": item["path"], "state": item}
            node["inputs"].append(entry)
            try:
                source = resolve_input(item, path, roots)
                entry["source"] = str(source)
            except ExportError as exc:
                if "Ambiguous" in str(exc):
                    raise
                gap(identity, "input-location", item["path"], str(exc))
                graph["external_inputs"].append(entry)
                continue
            if pp.get("path"):
                parent = source.parent / pp["path"]
                if not parent.is_file():
                    gap(identity, "parent-provenance", str(parent), "missing")
                    continue
                dep = visit(parent)
                if pp.get("sha256") and dep != pp["sha256"]:
                    gap(identity, "parent-provenance", str(parent), "hash-mismatch")
                entry["dependency"] = dep
                if dep not in node["dependencies"]:
                    node["dependencies"].append(dep)
            else:
                graph["external_inputs"].append(entry)
                if item.get("backend") == "git" and source.is_file():
                    collect(
                        source, identity, "configuration", (item.get("metadata", {}).get("file") or {}).get("sha256")
                    )
        return identity

    graph["roots"] = list(dict.fromkeys(visit(p) for p in selected))
    for identity in graph["roots"]:
        graph["products"][identity]["selected"] = True
    try:
        graph["order"] = list(
            TopologicalSorter({k: v["dependencies"] for k, v in graph["products"].items()}).static_order()
        )
    except CycleError as exc:
        raise ExportError(f"Provenance dependency cycle: {exc}") from exc
    return graph


def _archive_repositories(graph: dict[str, Any], output: Path, sources: dict[str, str]) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for repo in graph["repositories"]:
        name = str(repo.get("name") or repo.get("repo") or "repo")
        groups.setdefault(name, []).append(repo)
    for name, states in groups.items():
        remotes = {s["remote_url"].rstrip("/").removesuffix(".git") for s in states if s.get("remote_url")}
        if len(remotes) > 1:
            raise ExportError(f"Repository name collision: {name}")
        if not re.fullmatch(r"[\w.-]+", name) or name in {".", ".."}:
            raise ExportError(f"Unsafe repository name: {name}")
        commits = sorted({state["commit"] for state in states if state.get("commit")})
        if not commits:
            graph["gaps"].append({"role": "repository", "path": name, "reason": "no recorded commit"})
            continue
        source = sources.get(name) or next((s.get("remote_url") for s in states if s.get("remote_url")), None)
        if not source:
            graph["gaps"].append({"role": "repository", "path": name, "reason": "no source"})
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="reprotrail-git-") as temporary:
                repo = Path(temporary)
                _git("init", "--bare", str(repo))
                for index, commit in enumerate(commits):
                    # Fetch named commits explicitly, including commits unreachable from branches.
                    _git("fetch", str(source), f"{commit}:refs/heads/reprotrail-{index}", cwd=repo)
                bundle = output / "repositories" / f"{name}.bundle"
                bundle.parent.mkdir(parents=True, exist_ok=True)
                _git("bundle", "create", str(bundle), "--all", cwd=repo)
                for state in states:
                    state["archive"] = {"path": str(bundle.relative_to(output)), "sha256": sha256_file(bundle)}
        except (OSError, ExportError) as exc:
            graph["gaps"].append({"role": "repository", "path": name, "reason": str(exc)})


def export_products(
    *,
    provenances: list[str | Path],
    output: str | Path,
    input_roots: dict[str, str] | None = None,
    repo_sources: dict[str, str] | None = None,
    archive_repos: bool = False,
    strict: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Export selected data as symlinks and recursively copy reproduction metadata."""
    destination = Path(output).absolute()
    if os.path.lexists(destination):
        raise ExportError(f"Export destination already exists: {destination}")
    graph = discover_products(provenances, input_roots=input_roots)
    graph["archive_repos"] = archive_repos
    graph["dry_run"] = dry_run
    for node in graph["products"].values():
        if node["selected"]:
            parent = Path(graph["files"][node["provenance"]]["path"]).parent
            node["data_path"] = str(parent / Path(node["data"]).name)
            if node["data_path"] in {f["path"] for f in graph["files"].values()}:
                raise ExportError(f"Data/metadata destination collision: {node['data_path']}")
    if strict and graph["gaps"]:
        raise ExportError(f"Export has {len(graph['gaps'])} metadata gaps; inspect without --strict.")
    graph["status"] = "incomplete" if graph["gaps"] else "completed"
    if not dry_run:
        # Claim the destination before cleanup becomes our responsibility.
        destination.mkdir(parents=True)
        try:
            for original, ref in graph["files"].items():
                target = destination / ref["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
                if sha256_file(target) != ref["sha256"]:
                    raise ExportError(f"Source changed during export: {original}")
            for node in graph["products"].values():
                if node["selected"] and Path(node["data"]).exists():
                    (destination / node["data_path"]).symlink_to(
                        node["data"], target_is_directory=Path(node["data"]).is_dir()
                    )
            if archive_repos:
                _archive_repositories(graph, destination, repo_sources or {})
            if strict and graph["gaps"]:
                raise ExportError(f"Export has {len(graph['gaps'])} metadata gaps.")
            graph["status"] = "incomplete" if graph["gaps"] else "completed"
            write_json(destination / "manifest.json", graph)
            (destination / "README.md").write_text(
                "# Reprotrail export\n\nSelected data are symlinks; upstream data are excluded. "
                "Keep sources available during transfer. Pull with:\n\n"
                "```bash\nrsync -aL --partial host:/absolute/export-directory/ ./export-directory/\n```\n\n"
                "See manifest.json for dependencies, external inputs and metadata gaps. "
                "Original datasets and dependency package downloads remain external.\n",
                encoding="utf-8",
            )
        except BaseException:
            shutil.rmtree(destination)
            raise
    return graph


def load_export(bundle: str | Path) -> tuple[dict[str, Any], Path]:
    """Load an export, rejecting file paths that escape its directory."""
    path = Path(bundle).resolve()
    if path.is_dir():
        path /= "manifest.json"
    graph = read_json(path)
    if graph.get("kind") != "reprotrail-export" or graph.get("schema_version") != "1":
        raise ExportError("Unsupported export manifest.")
    root = path.parent
    refs = list(graph["files"].values()) + [r["archive"] for r in graph["repositories"] if r.get("archive")]
    for ref in refs:
        target = root / ref["path"]
        if not target.resolve().is_relative_to(root):
            raise ExportError(f"Metadata path escapes export: {ref['path']}")
    return graph, root
