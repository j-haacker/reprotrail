from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from reprotrail.provenance import (
    append_cf_history,
    append_xarray_history,
    build_cf_history_entry,
    canonicalize_remote_url,
    enforce_clean_repos,
    get_git_state,
    get_input_path_state,
    public_git_state,
    public_input_path_state,
    summarize_directory,
)


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init"], repo)
    _run(["git", "config", "user.email", "test@example.invalid"], repo)
    _run(["git", "config", "user.name", "Test User"], repo)
    (repo / "data.txt").write_text("clean\n", encoding="utf-8")
    _run(["git", "add", "data.txt"], repo)
    _run(["git", "commit", "-m", "initial"], repo)
    return repo


def test_get_git_state_clean_and_dirty(tmp_path):
    repo = _repo(tmp_path)
    clean = get_git_state(repo)
    assert clean.commit
    assert not clean.dirty
    assert clean.diff_hash is None

    (repo / "data.txt").write_text("dirty\n", encoding="utf-8")
    dirty = get_git_state(repo)
    assert dirty.dirty
    assert dirty.dirty_marker == "+dirty"
    assert dirty.diff_hash
    assert "data.txt" in dirty.status_short


def test_enforce_clean_repos_requires_allow_dirty(tmp_path):
    repo = _repo(tmp_path)
    (repo / "data.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="--allow-dirty"):
        enforce_clean_repos([repo])

    assert enforce_clean_repos([repo], allow_dirty=True)[0].dirty


def test_canonical_remote_and_public_git_state_omit_local_root(tmp_path):
    repo = _repo(tmp_path)
    _run(["git", "remote", "add", "origin", "github:example-org/example-project"], repo)

    state = public_git_state(get_git_state(repo))

    assert state["remote_url"] == "https://github.com/example-org/example-project"
    assert (
        canonicalize_remote_url("ssh://github/example-org/example-project.git")
        == "https://github.com/example-org/example-project"
    )
    assert state["name"] == "example-project"
    assert "repo_root" not in state


def test_history_helpers_strip_provenance_flags():
    entry = build_cf_history_entry(
        ["python", "-m", "tool", "--provenance-json", "run.prov.json"],
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert "python -m tool" in entry
    assert "--provenance-json" not in entry
    assert append_cf_history("old", "new") == "new\nold"


def test_xarray_history_attr_roundtrip():
    xr = pytest.importorskip("xarray")
    ds = xr.Dataset(attrs={"history": "old"})

    out = append_xarray_history(ds, "new", copy=True)

    assert out.attrs["history"] == "new\nold"
    assert ds.attrs["history"] == "old"


def test_synthetic_lfs_pointer_is_detected(tmp_path):
    pointer = tmp_path / "data.nc"
    pointer.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:0123456789abcdef\nsize 123\n",
        encoding="utf-8",
    )

    state = get_input_path_state(pointer)

    assert state.backend == "git-lfs"
    assert state.metadata["lfs"]["oid"] == "0123456789abcdef"


def test_synthetic_dvc_file_is_detected(tmp_path):
    repo = _repo(tmp_path)
    (repo / "data.bin.dvc").write_text(
        "outs:\n- md5: abc123\n  size: 9\n  path: data.bin\n",
        encoding="utf-8",
    )
    _run(["git", "add", "data.bin.dvc"], repo)
    _run(["git", "commit", "-m", "track dvc"], repo)

    state = get_input_path_state(repo / "data.bin")

    assert state.backend == "dvc"
    assert state.metadata["dvc"]["outputs"][0]["md5"] == "abc123"


def test_product_provenance_sidecar_is_detected(tmp_path):
    product = tmp_path / "effective-config.json"
    product.write_text("{}\n", encoding="utf-8")
    provenance = tmp_path / "effective-config.prov.json"
    provenance.write_text('{"schema_version": "1"}\n', encoding="utf-8")
    digest = hashlib.sha256(provenance.read_bytes()).hexdigest()
    (tmp_path / "effective-config.prov.json.sha256").write_text(
        f"{digest}  effective-config.prov.json\n",
        encoding="utf-8",
    )

    state = get_input_path_state(product)

    assert state.metadata["product_provenance"] == {
        "path": "effective-config.prov.json",
        "sha256": digest,
    }


def test_small_file_input_records_content_identity(tmp_path):
    source = tmp_path / "script.py"
    source.write_bytes(b"abc")

    state = get_input_path_state(source, max_file_hash_bytes=3)
    public = public_input_path_state(state)

    assert public["metadata"]["file"]["size_bytes"] == 3
    assert public["metadata"]["file"]["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert public["metadata"]["file"]["hash_kind"] == "sha256"


def test_large_file_input_skips_content_hash_at_declared_budget(tmp_path):
    source = tmp_path / "large.nc"
    source.write_bytes(b"abcd")

    state = get_input_path_state(source, max_file_hash_bytes=3)
    public = public_input_path_state(state)

    assert public["metadata"]["file"]["size_bytes"] == 4
    assert "sha256" not in public["metadata"]["file"]
    assert public["metadata"]["file"]["hash_skipped"] == {
        "reason": "size-limit",
        "max_bytes": 3,
    }


def test_large_file_inspection_bounds_git_lfs_prefix_read(tmp_path, monkeypatch):
    source = tmp_path / "large.nc"
    source.write_bytes(b"not a Git LFS pointer")
    resolved_source = source.resolve()
    real_open = Path.open
    read_sizes = []

    class BoundedReader:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self._handle.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self._handle, name)

        def read(self, size=-1):
            read_sizes.append(size)
            if size < 0 or size > 512:
                raise AssertionError("Git LFS inspection exceeded its prefix budget")
            return self._handle.read(size)

    def bounded_open(path, *args, **kwargs):
        handle = real_open(path, *args, **kwargs)
        if path.resolve() == resolved_source:
            return BoundedReader(handle)
        return handle

    monkeypatch.setattr(Path, "open", bounded_open)

    state = get_input_path_state(source, max_file_hash_bytes=0)

    assert state.backend == "filesystem"
    assert read_sizes == [512]


def test_directory_summary_stops_after_the_declared_entry_budget(tmp_path, monkeypatch):
    for name in ("a", "b", "c", "d", "e"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    real_scandir = os.scandir
    entries_yielded = []

    class CountingScandir:
        def __init__(self, path):
            self._iterator = real_scandir(path)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self._iterator.close()

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self._iterator)
            entries_yielded.append(entry.name)
            return entry

    monkeypatch.setattr("reprotrail.provenance.os.scandir", CountingScandir)

    summary = summarize_directory(tmp_path, max_entries=2)

    assert len(entries_yielded) == 3
    assert summary["manifest_truncated"] is True
    assert summary["max_entries"] == 2
    assert summary["entries_scanned"] == 3
    assert summary["manifest_entries"] == 2
    assert summary["manifest_hash_kind"] == "bounded-first-entries-paths-size-mtime-ns-v1"
    assert summary["file_count_at_least"] == 3
    assert summary["total_bytes_at_least"] == 3
    assert "file_count" not in summary
    assert "total_bytes" not in summary


def test_complete_directory_summary_preserves_global_path_hash(tmp_path):
    nested = tmp_path / "a"
    nested.mkdir()
    first = nested / "z"
    second = tmp_path / "b"
    first.write_text("x", encoding="utf-8")
    second.write_text("y", encoding="utf-8")
    for path in (first, second):
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))

    summary = summarize_directory(tmp_path, max_entries=10)

    assert summary["manifest_truncated"] is False
    assert summary["manifest_hash_kind"] == "paths-size-mtime-ns"
    assert summary["manifest_hash"] == "fc0fcedf7a42aae60e88352e57ae1a78f7ccc500341fdee76adfdd7cefe6c760"
