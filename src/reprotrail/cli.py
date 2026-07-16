"""Command-line interface for reprotrail."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .epochs import audit_dependency_epochs, check_dependency_contract
from .pixi import PixiGitFreshnessError, check_pixi_git_freshness
from .products import copy_readme_template, finalize_product_provenance
from .reproduce import ReproductionError, parse_key_value, reproduce_from_provenance
from .runner import RunError, run_from_namespace
from .settings import load_settings


def _cmd_run(args: argparse.Namespace) -> None:
    try:
        run_from_namespace(args)
    except RunError as err:
        raise SystemExit(str(err)) from err


def _cmd_finalize(args: argparse.Namespace) -> None:
    settings = load_settings()
    try:
        digest = finalize_product_provenance(
            args.provenance_json,
            project_root=settings.project_root,
            pixi_environment=settings.pixi_environment,
            allow_partial_metadata=args.allow_partial_metadata,
            stamp=not args.no_stamp,
        )
    except Exception as err:
        raise SystemExit(str(err)) from err
    if digest:
        print(digest)


def _cmd_template_readme(args: argparse.Namespace) -> None:
    try:
        path = copy_readme_template(args.output, force=args.force)
    except FileExistsError as err:
        raise SystemExit(str(err)) from err
    print(path)


def _cmd_reproduce(args: argparse.Namespace) -> None:
    try:
        report = reproduce_from_provenance(
            provenance=args.provenance,
            workspace=args.workspace,
            execute=args.execute,
            strict=args.strict,
            env=args.env,
            project_repo=args.project_repo,
            repo_sources=parse_key_value(args.repo_source),
            input_maps=parse_key_value(args.input_map),
            resume=args.resume,
            force=args.force,
        )
    except ReproductionError as err:
        raise SystemExit(str(err)) from err
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"reproduction status: {report['status']}")
        print(f"workspace: {report['workspace']}")
        print(f"report: {Path(report['workspace']) / 'REPRODUCTION.md'}")
    if report["status"] != "completed":
        raise SystemExit(1)


def _cmd_epoch_check(args: argparse.Namespace) -> None:
    settings = load_settings()
    try:
        result = check_dependency_contract(
            run_root=Path(args.run_root),
            project_root=settings.project_root,
            acceptance_reason=args.acceptance_reason,
            dry_run=args.dry_run,
            pixi_environment=args.env or settings.pixi_environment,
            package_names=settings.package_summary,
        )
    except Exception as err:
        raise SystemExit(str(err)) from err
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        status = result["status"]
        snapshot = result["snapshot"]["digest"]
        print(f"dependency epoch status: {status}")
        print(f"snapshot: {snapshot}")


def _cmd_epoch_audit(args: argparse.Namespace) -> None:
    settings = load_settings()
    report = audit_dependency_epochs(
        run_root=Path(args.run_root),
        output=Path(args.output),
        product_root_markers=settings.product_root_markers,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"dependency epoch audit: {args.output}")


def _format_source(source: dict[str, str]) -> str:
    label = source.get("url") or "git"
    revision = source.get("commit") or source.get("requested_revision")
    return f"{label}@{revision}" if revision else label


def _cmd_pixi_check_git_freshness(args: argparse.Namespace) -> None:
    try:
        result = check_pixi_git_freshness(
            project_root=Path.cwd(),
            environment=args.env,
            packages=tuple(args.package),
            manifest_path=Path(args.manifest_path) if args.manifest_path else None,
        )
    except PixiGitFreshnessError as err:
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "environment": args.env,
                        "checked_packages": list(args.package or []),
                        "error": str(err),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(str(err), file=sys.stderr)
        raise SystemExit(2) from err

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"pixi git freshness: {result['status']}")
        print(f"environment: {result['environment']}")
        print(f"checked packages: {', '.join(result['checked_packages'])}")
        if result["packages"]:
            print("stale packages:")
            for package in result["packages"]:
                print(
                    f"- {package['name']} ({package['platform']}): "
                    f"{_format_source(package['from'])} -> {_format_source(package['to'])}"
                )

    if result["status"] == "stale":
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reprotrail",
        description="Capture, audit, and reproduce provenance for data-processing runs.",
    )
    sub = parser.add_subparsers(dest="command_name", required=True, title="commands", metavar="COMMAND")

    run_description = "Run a command while recording its provenance and runtime state."
    run = sub.add_parser("run", help=run_description, description=run_description)
    run.add_argument(
        "--log",
        required=True,
        metavar="PATH",
        help="Write the wrapped command's combined stdout and stderr to PATH.",
    )
    run.add_argument(
        "--repo",
        action="append",
        metavar="PATH",
        help=(
            "Record PATH as diagnostic repository state instead of configured repository paths; "
            "repeat for multiple repositories."
        ),
    )
    run.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "Allow dirty trusted runtime repositories and record their patches instead of stopping before execution."
        ),
    )
    run.add_argument(
        "--allow-editable",
        action="store_true",
        help="Allow external editable/path Pixi dependencies when their Git provenance can be recorded.",
    )
    run.add_argument(
        "--allow-partial-metadata",
        action="store_true",
        help="Write partial product metadata when optional RO-Crate or SPDX tools are unavailable.",
    )
    run.add_argument(
        "--provenance-json",
        metavar="PATH",
        help=(
            "Write provenance JSON to PATH. If omitted, infer the path from --product-output or the wrapped "
            "command's --output."
        ),
    )
    run.add_argument(
        "--product-output",
        metavar="PATH",
        help="Treat PATH as the product to describe and finalize. If omitted, use the wrapped command's --output.",
    )
    run.add_argument(
        "--input",
        action="append",
        metavar="PATH",
        help="Snapshot PATH before execution as an input; repeat for multiple inputs and place each before --.",
    )
    run.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        metavar="COMMAND...",
        help="Command and arguments to run. Prefix with -- to separate them from reprotrail options.",
    )
    run.set_defaults(func=_cmd_run)

    finalize_description = "Finalize an existing product provenance record and its sidecars."
    finalize = sub.add_parser("finalize", help=finalize_description, description=finalize_description)
    finalize.add_argument(
        "--provenance-json",
        required=True,
        metavar="PATH",
        help="Read and finalize the product provenance record at PATH.",
    )
    finalize.add_argument(
        "--no-stamp",
        action="store_true",
        help=(
            "Do not write provenance pointer attributes into an existing Zarr or NetCDF product; "
            "sidecars are still finalized."
        ),
    )
    finalize.add_argument(
        "--allow-partial-metadata",
        action="store_true",
        help="Write partial product metadata when optional RO-Crate or SPDX tools are unavailable.",
    )
    finalize.set_defaults(func=_cmd_finalize)

    template_description = "Export bundled templates for product metadata files."
    template = sub.add_parser("template", help=template_description, description=template_description)
    template_sub = template.add_subparsers(dest="template_command", required=True, title="commands", metavar="COMMAND")

    readme_description = "Export the bundled product README template."
    readme = template_sub.add_parser("readme", help=readme_description, description=readme_description)
    readme.add_argument(
        "--output",
        required=True,
        metavar="PATH",
        help="Write the README template to PATH.",
    )
    readme.add_argument(
        "--force",
        action="store_true",
        help="Overwrite PATH if the README template already exists.",
    )
    readme.set_defaults(func=_cmd_template_readme)

    reproduce_description = "Create a reproduction workspace from a product provenance record."
    reproduce = sub.add_parser("reproduce", help=reproduce_description, description=reproduce_description)
    reproduce.add_argument(
        "--provenance",
        required=True,
        metavar="PATH",
        help="Read the product provenance record at PATH.",
    )
    reproduce.add_argument(
        "--workspace",
        required=True,
        metavar="PATH",
        help="Create the reproduction workspace at PATH.",
    )
    reproduce.add_argument(
        "--execute",
        action="store_true",
        help="Run the recorded command after restoring the workspace and installing the locked Pixi environment.",
    )
    reproduce.add_argument(
        "--strict",
        action="store_true",
        help="Treat every reproduction warning as a failure.",
    )
    reproduce.add_argument(
        "--env",
        metavar="ENV",
        help="Use Pixi environment ENV instead of the environment recorded in provenance.",
    )
    reproduce.add_argument(
        "--project-repo",
        metavar="NAME",
        help="Use recorded repository NAME as the project checkout when selecting from provenance.",
    )
    reproduce.add_argument(
        "--repo-source",
        action="append",
        metavar="NAME=SOURCE",
        help="Use local path or URL SOURCE for recorded repository NAME; repeat for multiple repositories.",
    )
    reproduce.add_argument(
        "--input-map",
        action="append",
        metavar="RECORDED=LOCAL",
        help=(
            "Replace exact recorded input path RECORDED with LOCAL in the reconstructed command and validation; "
            "repeat for multiple inputs."
        ),
    )
    reproduce.add_argument(
        "--resume",
        action="store_true",
        help="Reuse an existing workspace and continue restoration instead of rejecting it.",
    )
    reproduce.add_argument(
        "--force",
        action="store_true",
        help="Remove an existing workspace and recreate it from scratch.",
    )
    reproduce.add_argument(
        "--json",
        action="store_true",
        help="Print the reproduction report as JSON instead of a human-readable summary.",
    )
    reproduce.set_defaults(func=_cmd_reproduce)

    epoch_description = "Manage accepted dependency snapshots for run roots."
    epoch = sub.add_parser("epoch", help=epoch_description, description=epoch_description)
    epoch_sub = epoch.add_subparsers(dest="epoch_command", required=True, title="commands", metavar="COMMAND")

    check_description = "Check or accept the current dependency snapshot for a run root."
    check = epoch_sub.add_parser("check", help=check_description, description=check_description)
    check.add_argument(
        "--run-root",
        required=True,
        metavar="PATH",
        help="Check the dependency epoch contract under run-root directory PATH.",
    )
    check.add_argument(
        "--acceptance-reason",
        metavar="TEXT",
        help="Accept an unrecognized dependency snapshot and record TEXT as its reason.",
    )
    check.add_argument(
        "--dry-run",
        action="store_true",
        help="Report initialization or acceptance without writing the dependency epoch contract.",
    )
    check.add_argument(
        "--env",
        metavar="ENV",
        help="Use Pixi environment ENV instead of the configured environment.",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="Print the dependency epoch result as JSON instead of a human-readable summary.",
    )
    check.set_defaults(func=_cmd_epoch_check)

    audit_description = "Audit product provenance records against dependency epochs."
    audit = epoch_sub.add_parser("audit", help=audit_description, description=audit_description)
    audit.add_argument(
        "--run-root",
        required=True,
        metavar="PATH",
        help="Search run-root directory PATH for product provenance records.",
    )
    audit.add_argument(
        "--output",
        required=True,
        metavar="PATH",
        help="Write the dependency epoch audit report to PATH.",
    )
    audit.add_argument(
        "--json",
        action="store_true",
        help="Print the audit report as JSON in addition to writing it to --output.",
    )
    audit.set_defaults(func=_cmd_epoch_audit)

    pixi_description = "Inspect Pixi environments used by reproducible workflows."
    pixi = sub.add_parser("pixi", help=pixi_description, description=pixi_description)
    pixi_sub = pixi.add_subparsers(dest="pixi_command", required=True, title="commands", metavar="COMMAND")

    freshness_description = "Check whether selected Git-backed Pixi packages would move on update."
    freshness = pixi_sub.add_parser(
        "check-git-freshness",
        help=freshness_description,
        description=freshness_description,
    )
    freshness.add_argument(
        "--env",
        required=True,
        metavar="ENV",
        help="Check packages in Pixi environment ENV.",
    )
    freshness.add_argument(
        "--package",
        action="append",
        required=True,
        metavar="NAME",
        help="Check Git-backed package NAME; repeat for multiple packages.",
    )
    freshness.add_argument(
        "--manifest-path",
        metavar="PATH",
        help="Use PATH as the Pixi workspace directory or manifest. Defaults to the current working directory.",
    )
    freshness.add_argument(
        "--json",
        action="store_true",
        help="Print the freshness report as JSON instead of a human-readable summary.",
    )
    freshness.set_defaults(func=_cmd_pixi_check_git_freshness)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
