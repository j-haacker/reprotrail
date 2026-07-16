from __future__ import annotations

import argparse

import pytest

from reprotrail.cli import build_parser
from reprotrail.reproduce import parse_key_value


def _walk_parsers(parser: argparse.ArgumentParser):
    seen: set[int] = set()
    pending = [parser]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                pending.extend(action.choices.values())


def test_all_commands_and_arguments_have_help_descriptions():
    parsers = list(_walk_parsers(build_parser()))

    assert len(parsers) == 11
    assert all(parser.description for parser in parsers)

    command_choices = []
    user_arguments = []
    for parser in parsers:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                command_choices.extend(action._choices_actions)
            elif not isinstance(action, argparse._HelpAction):
                user_arguments.append(action)

    assert len(command_choices) == 10
    assert all(choice.help and choice.help != argparse.SUPPRESS for choice in command_choices)
    assert len(user_arguments) == 37
    assert all(action.help and action.help != argparse.SUPPRESS for action in user_arguments)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--help"], "Capture, audit, and reproduce provenance"),
        (["run", "--help"], "Run a command while recording its provenance"),
        (["finalize", "--help"], "Finalize an existing product provenance record"),
        (["template", "--help"], "Export bundled templates"),
        (["template", "readme", "--help"], "Export the bundled product README template"),
        (["reproduce", "--help"], "Create a reproduction workspace"),
        (["epoch", "--help"], "Manage accepted dependency snapshots"),
        (["epoch", "check", "--help"], "Check or accept the current dependency snapshot"),
        (["epoch", "audit", "--help"], "Audit product provenance records"),
        (["pixi", "--help"], "Inspect Pixi environments"),
        (["pixi", "check-git-freshness", "--help"], "Check whether selected Git-backed Pixi packages"),
    ],
)
def test_help_renders_at_every_command_level(argv, expected, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert expected in output
    if tuple(argv) in {("--help",), ("template", "--help"), ("epoch", "--help"), ("pixi", "--help")}:
        assert "commands:" in output


@pytest.mark.parametrize(
    ("argv", "expected_fragments"),
    [
        (
            ["run", "--help"],
            [
                "--log PATH",
                "--input PATH",
                "COMMAND...",
                "repeat for multiple inputs",
                "place each before --",
                "If omitted, infer the path",
                "allow dirty trusted runtime repositories",
            ],
        ),
        (
            ["finalize", "--help"],
            ["--provenance-json PATH", "--no-stamp", "sidecars are still finalized"],
        ),
        (
            ["reproduce", "--help"],
            [
                "--env ENV",
                "--repo-source NAME=SOURCE",
                "--input-map RECORDED=LOCAL",
                "environment recorded in provenance",
                "remove an existing workspace",
            ],
        ),
        (
            ["epoch", "check", "--help"],
            ["--acceptance-reason TEXT", "configured environment", "without writing"],
        ),
        (
            ["pixi", "check-git-freshness", "--help"],
            ["--package NAME", "repeat for multiple packages", "Defaults to the current working directory"],
        ),
    ],
)
def test_help_explains_value_shapes_fallbacks_and_safety(argv, expected_fragments, capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)

    output = " ".join(capsys.readouterr().out.split())
    for fragment in expected_fragments:
        assert " ".join(fragment.split()).lower() in output.lower()


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
            "--input",
            "source-a.nc",
            "--input",
            "source-b.nc",
            "--",
            "python",
            "-c",
            "pass",
            "--input",
            "child-input.nc",
        ]
    )
    assert run.command == ["--", "python", "-c", "pass", "--input", "child-input.nc"]
    assert run.allow_partial_metadata is True
    assert run.input == ["source-a.nc", "source-b.nc"]

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
            "downscale",
            "--package",
            "c4v-utils",
            "--package",
            "reprotrail",
            "--manifest-path",
            "pyproject.toml",
            "--json",
        ]
    )
    assert freshness.env == "downscale"
    assert freshness.package == ["c4v-utils", "reprotrail"]
    assert freshness.manifest_path == "pyproject.toml"
    assert freshness.json is True


def test_parser_requires_pixi_git_freshness_env_and_package():
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["pixi", "check-git-freshness", "--package", "c4v-utils"])
    with pytest.raises(SystemExit):
        parser.parse_args(["pixi", "check-git-freshness", "--env", "downscale"])
