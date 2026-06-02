from __future__ import annotations

import subprocess
from datetime import datetime, timezone

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
    _run(["git", "remote", "add", "origin", "github:j-haacker/reprotrail"], repo)

    state = public_git_state(get_git_state(repo))

    assert state["remote_url"] == "https://github.com/j-haacker/reprotrail"
    assert (
        canonicalize_remote_url("ssh://github/j-haacker/reprotrail.git")
        == "https://github.com/j-haacker/reprotrail"
    )
    assert state["name"] == "reprotrail"
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
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:0123456789abcdef\n"
        "size 123\n",
        encoding="utf-8",
    )

    state = get_input_path_state(pointer)

    assert state.backend == "git-lfs"
    assert state.metadata["lfs"]["oid"] == "0123456789abcdef"


def test_synthetic_dvc_file_is_detected(tmp_path):
    repo = _repo(tmp_path)
    (repo / "data.bin.dvc").write_text(
        "outs:\n"
        "- md5: abc123\n"
        "  size: 9\n"
        "  path: data.bin\n",
        encoding="utf-8",
    )
    _run(["git", "add", "data.bin.dvc"], repo)
    _run(["git", "commit", "-m", "track dvc"], repo)

    state = get_input_path_state(repo / "data.bin")

    assert state.backend == "dvc"
    assert state.metadata["dvc"]["outputs"][0]["md5"] == "abc123"
