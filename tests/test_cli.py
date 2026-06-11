from __future__ import annotations

import pytest

from reprotrail.cli import build_parser
from reprotrail.reproduce import parse_key_value


def test_parser_accepts_reproduce_command():
    parser = build_parser()

    args = parser.parse_args(
        [
            "reproduce",
            "--provenance",
            "hurs.prov.json",
            "--workspace",
            "workspace",
            "--execute",
            "--strict",
            "--env",
            "dev",
            "--repo-source",
            "main=/repo",
            "--input-map",
            "old=new",
            "--json",
        ]
    )

    assert args.provenance == "hurs.prov.json"
    assert args.workspace == "workspace"
    assert args.execute is True
    assert args.strict is True
    assert args.env == "dev"
    assert args.repo_source == ["main=/repo"]
    assert args.input_map == ["old=new"]
    assert args.json is True
    assert parse_key_value(args.repo_source) == {"main": "/repo"}


def test_parser_accepts_run_and_epoch_commands():
    parser = build_parser()

    run = parser.parse_args(
        [
            "run",
            "--log",
            "run.log",
            "--allow-partial-metadata",
            "--provenance-json",
            "run.prov.json",
            "--",
            "python",
            "-c",
            "pass",
        ]
    )
    assert run.command == ["--", "python", "-c", "pass"]
    assert run.allow_partial_metadata is True

    finalize = parser.parse_args(
        [
            "finalize",
            "--provenance-json",
            "run.prov.json",
            "--allow-partial-metadata",
        ]
    )
    assert finalize.allow_partial_metadata is True

    template = parser.parse_args(["template", "readme", "--output", "README.md.template", "--force"])
    assert template.output == "README.md.template"
    assert template.force is True

    check = parser.parse_args(["epoch", "check", "--run-root", "run", "--dry-run"])
    assert check.run_root == "run"
    assert check.dry_run is True

    audit = parser.parse_args(["epoch", "audit", "--run-root", "run", "--output", "audit.json"])
    assert audit.output == "audit.json"

    freshness = parser.parse_args(
        [
            "pixi",
            "check-git-freshness",
            "--env",
            "analysis",
            "--package",
            "example-library",
            "--package",
            "reprotrail",
            "--manifest-path",
            "pyproject.toml",
            "--json",
        ]
    )
    assert freshness.env == "analysis"
    assert freshness.package == ["example-library", "reprotrail"]
    assert freshness.manifest_path == "pyproject.toml"
    assert freshness.json is True


def test_parser_requires_pixi_git_freshness_env_and_package():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["pixi", "check-git-freshness", "--package", "example-library"])
    with pytest.raises(SystemExit):
        parser.parse_args(["pixi", "check-git-freshness", "--env", "analysis"])
