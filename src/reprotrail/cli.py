"""Command-line interface for reprotrail."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .epochs import audit_dependency_epochs, check_dependency_contract
from .products import finalize_product_provenance
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
            license=settings.license,
            stamp=not args.no_stamp,
        )
    except Exception as err:
        raise SystemExit(str(err)) from err
    if digest:
        print(digest)


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reprotrail")
    sub = parser.add_subparsers(dest="command_name", required=True)

    run = sub.add_parser("run")
    run.add_argument("--log", required=True)
    run.add_argument("--repo", action="append")
    run.add_argument("--allow-dirty", action="store_true")
    run.add_argument("--allow-editable", action="store_true")
    run.add_argument("--provenance-json")
    run.add_argument("--product-output")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=_cmd_run)

    finalize = sub.add_parser("finalize")
    finalize.add_argument("--provenance-json", required=True)
    finalize.add_argument("--no-stamp", action="store_true")
    finalize.set_defaults(func=_cmd_finalize)

    reproduce = sub.add_parser("reproduce")
    reproduce.add_argument("--provenance", required=True)
    reproduce.add_argument("--workspace", required=True)
    reproduce.add_argument("--execute", action="store_true")
    reproduce.add_argument("--strict", action="store_true")
    reproduce.add_argument("--env")
    reproduce.add_argument("--project-repo")
    reproduce.add_argument("--repo-source", action="append")
    reproduce.add_argument("--input-map", action="append")
    reproduce.add_argument("--resume", action="store_true")
    reproduce.add_argument("--force", action="store_true")
    reproduce.add_argument("--json", action="store_true")
    reproduce.set_defaults(func=_cmd_reproduce)

    epoch = sub.add_parser("epoch")
    epoch_sub = epoch.add_subparsers(dest="epoch_command", required=True)

    check = epoch_sub.add_parser("check")
    check.add_argument("--run-root", required=True)
    check.add_argument("--acceptance-reason")
    check.add_argument("--dry-run", action="store_true")
    check.add_argument("--env")
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=_cmd_epoch_check)

    audit = epoch_sub.add_parser("audit")
    audit.add_argument("--run-root", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(func=_cmd_epoch_audit)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
