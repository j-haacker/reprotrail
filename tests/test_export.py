from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from reprotrail._json import read_json, write_json
from reprotrail._paths import sha256_file
from reprotrail.export import ExportError, discover_products, export_products
from reprotrail.reproduce import reproduce_from_provenance


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def chain(tmp_path):
    repo = tmp_path / "code"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "producer.py").write_text("""import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--input', nargs='+')
p.add_argument('--output')
p.add_argument('--qc')
a = p.parse_args()
out = Path(a.output)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(''.join(Path(x).read_text() for x in a.input) + '!')
out.with_suffix('.prov.json').write_text(json.dumps({'output': str(out)}))
if a.qc:
    Path(a.qc).write_text('ok')
""")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "producer")
    commit = git(repo, "rev-parse", "HEAD")
    root = tmp_path / "data"
    root.mkdir()
    raw = root / "raw.txt"
    raw.write_text("input")
    lock = root / "pixi.lock"
    lock.write_text("lock")
    summary = root / "environment.json"
    write_json(summary, {"pixi": {"environment": "test"}})
    nodes = {}

    def product(name, parents):
        directory = root / name
        directory.mkdir()
        data = directory / f"{name}.txt"
        data.write_text(name)
        provenance = directory / f"{name}.prov.json"
        inputs = []
        for source in parents:
            state = {"path": str(source), "metadata": {"file": {"sha256": sha256_file(source)}}}
            pp = source.with_suffix(".prov.json")
            if pp.exists():
                state["metadata"]["product_provenance"] = {"path": pp.name, "sha256": sha256_file(pp)}
            inputs.append(state)
        record = {
            "command": [
                "python",
                "producer.py",
                "--input",
                *map(str, parents),
                "--output",
                str(data),
                "--qc",
                str(root / (name + ".qc.json")),
            ],
            "product": {"data": data.name},
            "project_repo": {"name": "code", "commit": commit, "remote_url": str(repo)},
            "environment": {
                "lockfile": {"path": str(lock), "sha256": sha256_file(lock)},
                "summary": {"path": str(summary), "sha256": sha256_file(summary)},
            },
            "input_paths": inputs,
        }
        write_json(provenance, record)
        provenance.with_name(provenance.name + ".sha256").write_text(f"{sha256_file(provenance)}  {provenance.name}\n")
        nodes[name] = (data, provenance)
        return data

    a = product("a", [raw])
    b = product("b", [a])
    product("c", [a, b])
    return root, repo, nodes, raw, product


def export_chain(chain, tmp_path, **kwargs):
    root, repo, nodes, raw, _ = chain
    output = tmp_path / "export"
    report = export_products(provenances=[nodes["c"][1]], output=output, **kwargs)
    return output, report


def fake_pixi(monkeypatch):
    import reprotrail.reproduce as module

    original = module._run
    invocations = []

    def run(command, **kwargs):
        if command[:2] == ["pixi", "install"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["pixi", "run"]:
            invocations.append(command)
            return original([sys.executable, *command[5:]], **kwargs)
        return original(command, **kwargs)

    monkeypatch.setattr(module, "_run", run)
    return invocations


def test_export_symlinks_only_selected_and_preserves_metadata(chain, tmp_path):
    _, _, nodes, _, _ = chain
    original = {name: p.read_bytes() for name, (_, p) in nodes.items()}
    output, report = export_chain(chain, tmp_path)
    assert len(report["products"]) == 3
    assert len(report["roots"]) == 1
    assert report["gaps"] == []
    assert not (output / "repositories").exists()
    for node in report["products"].values():
        archived = output / report["files"][node["provenance"]]["path"]
        assert archived.read_bytes() == Path(node["provenance"]).read_bytes()
        if node["selected"]:
            assert node["data_path"].startswith("products/")
            assert archived.parent == (output / node["data_path"]).parent
            assert (output / node["data_path"]).is_symlink()
        else:
            assert report["files"][node["provenance"]]["path"].startswith("metadata/")
            assert not (archived.parent / Path(node["data"]).name).exists()
    assert all(p.read_bytes() == original[name] for name, (_, p) in nodes.items())
    assert "rsync -aL" in (output / "README.md").read_text()
    assert (output / "manifest.json").exists()


def test_dry_run_and_existing_destination(chain, tmp_path):
    output, report = export_chain(chain, tmp_path, dry_run=True)
    assert not output.exists()
    assert report["dry_run"]
    output.mkdir()
    with pytest.raises(ExportError, match="already exists"):
        export_chain(chain, tmp_path)


def test_missing_parent_and_mismatched_summary(chain, tmp_path):
    root, _, nodes, _, _ = chain
    (root / "environment.json").write_text("{}")
    nodes["a"][1].unlink()
    _, report = export_chain(chain, tmp_path, dry_run=True)
    assert {g["reason"] for g in report["gaps"]} == {"missing", "hash-mismatch"}
    with pytest.raises(ExportError, match="metadata gaps"):
        export_chain(chain, tmp_path, strict=True)
    assert not (tmp_path / "export").exists()


def test_cycle_is_rejected(chain):
    _, _, nodes, _, _ = chain
    data, path = nodes["a"]
    record = read_json(path)
    record["input_paths"].append(
        {"path": str(nodes["c"][0]), "metadata": {"product_provenance": {"path": nodes["c"][1].name}}}
    )
    write_json(path, record)
    with pytest.raises(ExportError, match="cycle"):
        discover_products([nodes["c"][1]])


def test_ambiguous_relative_input_requires_root(chain, tmp_path, monkeypatch):
    _, _, nodes, _, _ = chain
    path = nodes["a"][1]
    record = read_json(path)
    record["input_paths"] = [{"path": "raw.txt"}]
    write_json(path, record)
    (path.parent / "raw.txt").write_text("different")
    (tmp_path / "raw.txt").write_text("different")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ExportError, match="Ambiguous"):
        discover_products([path])


def test_archive_multiple_and_unreferenced_commits(chain, tmp_path):
    _, repo, nodes, _, _ = chain
    old = git(repo, "rev-parse", "HEAD")
    (repo / "extra").write_text("second")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "second")
    extra = git(repo, "rev-parse", "HEAD")
    git(repo, "reset", "--soft", old)
    path = nodes["c"][1]
    record = read_json(path)
    record["configured_repos"] = [{"name": "code", "commit": extra, "remote_url": str(repo)}]
    # This runtime-only repository must never be fetched or bundled.
    record["dependency_snapshot"] = {"runtime_packages": [{"name": "runtime", "direct_url": {"url": "bad"}}]}
    write_json(path, record)
    path.with_name(path.name + ".sha256").write_text(sha256_file(path))
    output, report = export_chain(chain, tmp_path, archive_repos=True)
    assert not report["gaps"]
    bundles = list((output / "repositories").glob("*"))
    assert len(bundles) == 1
    restored = tmp_path / "restored"
    subprocess.run(["git", "clone", str(bundles[0]), str(restored)], check=True, capture_output=True)
    assert git(restored, "cat-file", "-t", old) == "commit"
    assert git(restored, "cat-file", "-t", extra) == "commit"


@pytest.mark.parametrize("layout", ["default", "short", "legacy"])
def test_replay_relocated_export_in_dependency_order(chain, tmp_path, monkeypatch, layout):
    root, repo, nodes, raw, _ = chain
    output, graph = export_chain(
        chain,
        tmp_path,
        archive_repos=True,
        input_roots={"data": str(root)},
        product_base=root if layout == "short" else None,
    )
    if layout == "legacy":
        # Recreate schema-1 packages whose selected payloads lived under metadata/.
        for ref in graph["files"].values():
            if ref["path"].startswith("products/"):
                legacy_path = "metadata/" + ref["path"].removeprefix("products/")
                target = output / legacy_path
                target.parent.mkdir(parents=True, exist_ok=True)
                (output / ref["path"]).rename(target)
                ref["path"] = legacy_path
        for node in graph["products"].values():
            if node["selected"]:
                legacy_path = "metadata/" + node["data_path"].removeprefix("products/")
                (output / node["data_path"]).rename(output / legacy_path)
                node["data_path"] = legacy_path
        write_json(output / "manifest.json", graph)
    moved = tmp_path / "moved"
    # Mirrors rsync -aL: selected symlink payload becomes real data at destination.
    shutil.copytree(output, moved, symlinks=False)
    root.rename(tmp_path / "unavailable-data")
    repo.rename(tmp_path / "unavailable-code")
    calls = fake_pixi(monkeypatch)
    selected = next(n for n in graph["products"].values() if n["selected"])
    provenance = moved / graph["files"][selected["provenance"]]["path"]
    work = tmp_path / "replay"
    report = reproduce_from_provenance(
        provenance=provenance,
        workspace=work,
        bundle=moved,
        execute=True,
        install=False,
        input_maps={str(raw): str(tmp_path / "unavailable-data/raw.txt")},
    )
    assert report["status"] == "completed", report
    assert len(calls) == 3
    assert [s["id"] for s in report["steps"]] == graph["order"]
    assert report["substitutions"]
    assert Path(report["steps"][-1]["output"]).read_text() == "input!input!!!"
    assert not (root / "c.qc.json").exists()
    assert all(Path(c[-1]).is_relative_to(work) for c in calls)
    again = reproduce_from_provenance(
        provenance=provenance,
        workspace=work,
        bundle=moved,
        execute=True,
        install=False,
        resume=True,
        input_maps={str(raw): str(tmp_path / "unavailable-data/raw.txt")},
    )
    assert again["status"] == "completed", again
    assert len(calls) == 3
    assert all(s["status"] == "resumed" for s in again["steps"])


def test_reuse_prunes_missing_ancestors(chain, tmp_path, monkeypatch):
    _, _, nodes, raw, _ = chain
    # b is usable but its ancestor a is unavailable. c also needs a, so target b.
    raw.unlink()
    nodes["a"][0].unlink()
    # Recreate a valid consumer of b only.
    _, _, _, _, product = chain
    target = product("d", [nodes["b"][0]])
    calls = fake_pixi(monkeypatch)
    report = reproduce_from_provenance(
        provenance=target.with_suffix(".prov.json"),
        workspace=tmp_path / "replay",
        recursive=True,
        execute=True,
        install=False,
    )
    assert report["status"] == "completed", report
    assert len(calls) == 1
    assert [s["status"] for s in report["steps"]] == ["reused", "completed"]


def test_recursive_plan_does_not_install_or_execute(chain, tmp_path, monkeypatch):
    _, _, nodes, _, _ = chain
    calls = fake_pixi(monkeypatch)
    report = reproduce_from_provenance(provenance=nodes["c"][1], workspace=tmp_path / "plan", recursive=True)
    assert report["status"] == "planned"
    assert not calls
    assert not (tmp_path / "plan/steps").exists()


def test_gaps_block_without_acceptance_and_missing_inputs_always_block(chain, tmp_path, monkeypatch):
    root, _, nodes, raw, _ = chain
    write_json(root / "environment.json", {"pixi": {"environment": "changed"}})
    fake_pixi(monkeypatch)
    report = reproduce_from_provenance(provenance=nodes["c"][1], workspace=tmp_path / "blocked", recursive=True)
    assert report["status"] == "blocked"
    report = reproduce_from_provenance(
        provenance=nodes["c"][1],
        workspace=tmp_path / "accepted",
        recursive=True,
        accept_incomplete=True,
    )
    assert report["status"] == "planned", report
    nodes["a"][0].unlink()
    nodes["b"][0].unlink()
    raw.unlink()
    report = reproduce_from_provenance(
        provenance=nodes["c"][1],
        workspace=tmp_path / "missing",
        recursive=True,
        accept_incomplete=True,
    )
    assert report["status"] == "blocked"
    assert "External input missing" in report["blockers"][0]


def test_replay_each_steps_recorded_commit_and_environment(chain, tmp_path, monkeypatch):
    root, repo, nodes, _, _ = chain
    script = repo / "producer.py"
    script.write_text(script.read_text().replace(" + '!'", " + '?'"))
    git(repo, "add", ".")
    git(repo, "commit", "-m", "changed algorithm")
    summary = root / "other-environment.json"
    write_json(summary, {"pixi": {"environment": "other"}})
    path = nodes["c"][1]
    record = read_json(path)
    record["project_repo"]["commit"] = git(repo, "rev-parse", "HEAD")
    record["environment"]["summary"] = {"path": str(summary), "sha256": sha256_file(summary)}
    write_json(path, record)
    path.with_name(path.name + ".sha256").write_text(sha256_file(path))
    nodes["a"][0].unlink()
    nodes["b"][0].unlink()
    calls = fake_pixi(monkeypatch)
    report = reproduce_from_provenance(
        provenance=path,
        workspace=tmp_path / "replay",
        recursive=True,
        execute=True,
        install=False,
    )
    assert report["status"] == "completed", report
    assert [c[3] for c in calls] == ["test", "test", "other"]
    assert Path(report["steps"][-1]["output"]).read_text() == "input!input!!?"


def test_resume_invalidates_tampered_output(chain, tmp_path, monkeypatch):
    _, _, nodes, _, _ = chain
    nodes["a"][0].unlink()
    nodes["b"][0].unlink()
    calls = fake_pixi(monkeypatch)
    args = dict(provenance=nodes["c"][1], workspace=tmp_path / "replay", recursive=True, execute=True, install=False)
    report = reproduce_from_provenance(**args)
    assert report["status"] == "completed", report
    Path(report["steps"][-1]["output"]).write_text("tampered")
    again = reproduce_from_provenance(**args, resume=True)
    assert again["status"] == "completed", again
    assert len(calls) == 4
    assert again["steps"][-1]["status"] == "completed"
    assert all(s["status"] == "resumed" for s in again["steps"][:-1])


def test_producer_failure_stops_descendants(chain, tmp_path, monkeypatch):
    import reprotrail.reproduce as module

    _, _, nodes, _, _ = chain
    nodes["a"][0].unlink()
    nodes["b"][0].unlink()
    fake_pixi(monkeypatch)
    original = module._run

    def run(command, **kwargs):
        if command[:2] == ["pixi", "run"]:
            raise module.ReproductionError("producer failed")
        return original(command, **kwargs)

    monkeypatch.setattr(module, "_run", run)
    report = reproduce_from_provenance(
        provenance=nodes["c"][1],
        workspace=tmp_path / "replay",
        recursive=True,
        execute=True,
        install=False,
    )
    assert report["status"] == "blocked"
    assert len(report["steps"]) == 1
    assert "producer failed" in report["blockers"][0]


def test_manifest_paths_cannot_escape_export(chain, tmp_path):
    from reprotrail.export import load_export

    output, graph = export_chain(chain, tmp_path)
    next(iter(graph["files"].values()))["path"] = "../outside.json"
    write_json(output / "manifest.json", graph)
    with pytest.raises(ExportError, match="escapes export"):
        load_export(output)


def test_catalog_driven_data_root_and_tracked_configuration(chain, tmp_path, monkeypatch):
    root, repo, nodes, raw, _ = chain
    (repo / "settings.txt").write_text("configuration")
    (repo / "producer.py").write_text("""import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--data-root')
p.add_argument('--settings')
p.add_argument('--output')
a = p.parse_args()
out = Path(a.output)
out.write_text((Path(a.data_root) / 'raw.txt').read_text() + Path(a.settings).read_text())
out.with_suffix('.prov.json').write_text('{}')
""")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "catalog-driven producer")
    path = nodes["a"][1]
    record = read_json(path)
    record["project_repo"]["commit"] = git(repo, "rev-parse", "HEAD")
    record["input_paths"][0]["path"] = "raw.txt"
    record["input_paths"][0]["git"] = {"name": "inputs"}
    record["input_paths"].append(
        {
            "path": "settings.txt",
            "backend": "git",
            "git": {"name": "code"},
            "metadata": {"file": {"sha256": sha256_file(repo / "settings.txt")}},
        }
    )
    record["command"] = [
        "python",
        "producer.py",
        "--data-root",
        "../inputs",
        "--settings",
        str(repo / "settings.txt"),
        "--output",
        str(nodes["a"][0]),
    ]
    write_json(path, record)
    path.with_name(path.name + ".sha256").write_text(sha256_file(path))
    fake_pixi(monkeypatch)
    report = reproduce_from_provenance(
        provenance=path,
        workspace=tmp_path / "replay",
        recursive=True,
        execute=True,
        install=False,
        input_roots={"inputs": str(root), "code": str(repo)},
    )
    assert report["status"] == "completed", report
    assert Path(report["steps"][0]["output"]).read_text() == "inputconfiguration"
    assert raw.read_text() == "input"
    assert (tmp_path / "replay/inputs" / report["target"] / "inputs/raw.txt").is_symlink()


def test_archived_git_dependencies_route_locked_downloads_without_lock_changes(chain, tmp_path):
    from reprotrail.reproduce import _archived_git_environment

    _, repo, _, _, _ = chain
    bundle = tmp_path / "code.bundle"
    git(repo, "bundle", "create", str(bundle), "HEAD")
    lock = tmp_path / "locked"
    original = "pypi: git+ssh://github/example/code.git?branch=main#" + git(repo, "rev-parse", "HEAD")
    lock.write_text(original)
    record = {"configured_repos": [{"name": "code", "remote_url": "https://github.com/example/code"}]}
    report = {"commands": [], "adaptations": []}
    environment = _archived_git_environment(record, {"code": str(bundle)}, lock, tmp_path / "artifacts", report)
    assert environment is not None
    cloned = tmp_path / "offline-clone"
    subprocess.run(
        ["git", "clone", "ssh://github/example/code.git", str(cloned)], env=environment, check=True, capture_output=True
    )
    assert git(cloned, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
    assert lock.read_text() == original
    assert report["adaptations"][0]["kind"] == "archived-git-dependencies"


def test_malformed_provenance_and_data_metadata_collision(chain, tmp_path):
    _, _, nodes, _, _ = chain
    path = nodes["c"][1]
    path.write_text("[]")
    with pytest.raises(ExportError, match="Malformed"):
        discover_products([path])
    write_json(path, {"command": ["producer"], "product": {"data": path.name}})
    path.with_name(path.name + ".sha256").write_text(sha256_file(path))
    with pytest.raises(ExportError, match="collision"):
        export_chain(chain, tmp_path)


def test_force_overrides_resume_and_changed_patch_blocks_reuse(chain, tmp_path, monkeypatch):
    root, repo, nodes, _, _ = chain
    script = repo / "producer.py"
    original = script.read_text()
    script.write_text(original.replace(" + '!'", " + '?'"))
    patch = root / "producer.patch"
    patch.write_text(git(repo, "diff", "--binary") + "\n")
    script.write_text(original)
    path = nodes["a"][1]
    record = read_json(path)
    record["project_repo"].update(dirty=True, patch={"path": str(patch), "sha256": sha256_file(patch)})
    write_json(path, record)
    path.with_name(path.name + ".sha256").write_text(sha256_file(path))
    calls = fake_pixi(monkeypatch)
    work = tmp_path / "replay"
    args = dict(provenance=path, workspace=work, recursive=True, execute=True, install=False)
    first = reproduce_from_provenance(**args)
    assert first["status"] == "completed", first
    assert Path(first["steps"][0]["output"]).read_text() == "input?"
    marker = work / "old-marker"
    marker.write_text("remove with force")
    second = reproduce_from_provenance(**args, resume=True, force=True)
    assert second["status"] == "completed", second
    assert not marker.exists()
    assert len(calls) == 2
    patch.write_text(patch.read_text() + "\n")
    blocked = reproduce_from_provenance(**args, resume=True)
    assert blocked["status"] == "blocked"
    assert len(calls) == 2


def test_bundle_external_repository_root_mapping(chain, tmp_path, monkeypatch):
    root, _, nodes, raw, _ = chain
    # Rewrite the raw leaf as a repository-relative input and update ancestor links.
    for name in ("a", "b", "c"):
        path = nodes[name][1]
        record = read_json(path)
        for state in record["input_paths"]:
            if state["path"] == str(raw):
                state["path"] = "raw.txt"
                state["git"] = {"name": "inputs"}
            parent = (state.get("metadata") or {}).get("product_provenance")
            if parent:
                parent["sha256"] = sha256_file(Path(state["path"]).parent / parent["path"])
        write_json(path, record)
        path.with_name(path.name + ".sha256").write_text(sha256_file(path))
    output, graph = export_chain(chain, tmp_path, input_roots={"inputs": str(root)})
    archive = tmp_path / "external-inputs"
    archive.mkdir()
    shutil.copyfile(raw, archive / "raw.txt")
    root.rename(tmp_path / "source-unavailable")
    calls = fake_pixi(monkeypatch)
    target = graph["products"][graph["roots"][0]]
    report = reproduce_from_provenance(
        provenance=output / graph["files"][target["provenance"]]["path"],
        bundle=output,
        workspace=tmp_path / "replay",
        execute=True,
        install=False,
        input_roots={"inputs": str(archive)},
    )
    assert report["status"] == "completed", report
    assert len(calls) == 3


def test_export_does_not_remove_a_concurrently_created_destination(chain, tmp_path, monkeypatch):
    output = tmp_path / "export"
    original = Path.mkdir

    def mkdir(path, *args, **kwargs):
        if path == output:
            original(path)
            (path / "owned-by-someone-else").write_text("keep")
            raise FileExistsError(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(FileExistsError):
        export_chain(chain, tmp_path)
    assert (output / "owned-by-someone-else").read_text() == "keep"


def test_force_refuses_workspace_containing_external_inputs(chain, tmp_path):
    from reprotrail.reproduce import ReproductionError

    _, _, nodes, raw, _ = chain
    work = tmp_path / "raw-archive"
    work.mkdir()
    source = work / "raw.txt"
    shutil.copyfile(raw, source)
    path = nodes["a"][1]
    record = read_json(path)
    record["input_paths"][0]["path"] = str(source)
    write_json(path, record)
    path.with_name(path.name + ".sha256").write_text(sha256_file(path))
    with pytest.raises(ReproductionError, match="encloses source"):
        reproduce_from_provenance(provenance=path, workspace=work, recursive=True, force=True)
    assert source.read_text() == "input"


def test_product_base_shortens_selected_packages_and_keeps_sidecars_adjacent(chain, tmp_path):
    root, _, nodes, _, _ = chain
    package = nodes["c"][1].parent
    for name in ("README.md", "LICENSE.md", "ro-crate-metadata.json"):
        (package / name).write_text(name)
    original = {p.name: p.read_bytes() for p in package.iterdir() if p.is_file()}
    output = tmp_path / "export"
    arguments = dict(provenances=[nodes["a"][1], nodes["c"][1]], output=output, product_base=root)
    planned = export_products(**arguments, dry_run=True)
    assert not output.exists()
    actual = export_products(**arguments)
    assert actual["schema_version"] == "1"
    assert actual["files"] == planned["files"]
    assert actual["products"] == planned["products"]
    for name in ("a", "c"):
        assert (output / f"products/{name}/{name}.txt").is_symlink()
        assert (output / f"products/{name}/{name}.prov.json").is_file()
        assert (output / f"products/{name}/{name}.prov.json.sha256").is_file()
    for name, content in original.items():
        assert (output / "products/c" / name).read_bytes() == content
    selected_sources = {node["provenance"] for node in actual["products"].values() if node["selected"]}
    assert all(actual["files"][source]["path"].startswith("products/") for source in selected_sources)
    assert not (output / "products/b").exists()
    readme = (output / "README.md").read_text()
    assert "[products/c/c.txt](products/c/c.txt)" in readme
    assert "[Provenance](products/c/c.prov.json)" in readme
    assert "metadata/" in readme


def test_default_product_layout_preserves_repository_relative_paths(chain, tmp_path):
    root, _, nodes, _, _ = chain
    output, report = export_chain(chain, tmp_path, input_roots={"data": str(root)})
    assert (output / "products/data/c/c.txt").is_symlink()
    assert (output / "products/data/c/c.prov.json").is_file()
    for node in report["products"].values():
        if not node["selected"]:
            assert report["files"][node["provenance"]]["path"].startswith("metadata/data/")


@pytest.mark.parametrize("dry_run", [False, True])
def test_product_base_rejects_packages_outside_base_before_writing(chain, tmp_path, dry_run):
    root, _, nodes, _, _ = chain
    output = tmp_path / "export"
    with pytest.raises(ExportError, match="outside --product-base"):
        export_products(
            provenances=[nodes["a"][1], nodes["c"][1]], output=output, product_base=root / "c", dry_run=dry_run
        )
    assert not output.exists()


def test_short_export_reuses_selected_ancestor_after_transfer(chain, tmp_path, monkeypatch):
    root, _, nodes, _, _ = chain
    source = tmp_path / "export"
    graph = export_products(provenances=[nodes["a"][1], nodes["c"][1]], output=source, product_base=root)
    transferred = tmp_path / "transferred"
    shutil.copytree(source, transferred, symlinks=False)
    root.rename(tmp_path / "unavailable-inputs")
    calls = fake_pixi(monkeypatch)
    target = next(node for node in graph["products"].values() if node["provenance"] == str(nodes["c"][1]))
    report = reproduce_from_provenance(
        provenance=transferred / graph["files"][target["provenance"]]["path"],
        bundle=transferred,
        workspace=tmp_path / "replay",
        execute=True,
        install=False,
    )
    assert report["status"] == "completed", report
    assert [step["status"] for step in report["steps"]] == ["reused", "completed", "completed"]
    assert len(calls) == 2
    assert Path(report["steps"][-1]["output"]).read_text() == "aa!!"
