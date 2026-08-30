"""CLI surface for catalog-first RGAA scans and declared page samples."""

from __future__ import annotations

import argparse
from typing import cast

from cdpx import scenarios
from cdpx.commands.shared import assert_session_current, browser_client, emit_json, execution
from cdpx.policy import Authority, assert_url_allowed
from cdpx.primitives import nav
from cdpx.rgaa.catalog import CatalogError, describe_catalog, parse_test_selection
from cdpx.rgaa.sample import compile_sample, run_sample
from cdpx.rgaa.scanner import Engine, Scope, scan


def _tests_arg(value: str) -> tuple[str, ...]:
    try:
        return parse_test_selection(value) or ()
    except CatalogError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _required_authority(scope: str, engine: str) -> Authority:
    if engine == "hybrid" or scope == "privileged":
        return Authority.PRIVILEGED
    if scope == "interactive":
        return Authority.INTERACTION
    return Authority.OBSERVATION


def cmd_catalog(args) -> None:
    emit_json(args, describe_catalog(args.options.tests))


def cmd_scan(args) -> None:
    selected = args.options.tests
    required = _required_authority(args.options.scope, args.options.engine)
    context = execution(args)
    if args.options.url:
        assert_url_allowed(args.options.url, context.origins)
    with browser_client(args, required_authority=required) as client:
        if args.options.url:
            nav.navigate(client, args.options.url, wait="load", timeout=args.options.timeout)
        assert_session_current(args, client)
        result = scan(
            client,
            scope=cast(Scope, args.options.scope),
            engine=cast(Engine, args.options.engine),
            selected_tests=selected,
            timeout=args.options.timeout,
        )
        assert_session_current(args, client)
        emit_json(args, result)


def _compile_or_usage(path: str | None):
    if path is None:
        raise scenarios.ScenarioUsageError("RGAA sample path required")
    try:
        return compile_sample(path)
    except ValueError as error:
        raise scenarios.ScenarioUsageError(str(error)) from error


def cmd_sample_validate(args) -> None:
    compiled = _compile_or_usage(args.options.path)
    emit_json(args, compiled.public_plan())


def cmd_sample_run(args) -> None:
    compiled = _compile_or_usage(args.options.path)
    context = execution(args)
    for page in compiled.pages:
        assert_url_allowed(page.url, context.origins)
    with browser_client(args, required_authority=compiled.authority) as client:
        result = run_sample(
            client,
            compiled,
            timeout=args.options.timeout,
            origin_guard=lambda: assert_session_current(args, client),
        )
        assert_session_current(args, client)
        emit_json(args, result)


def register_commands(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = sub.add_parser(
        "rgaa",
        help="catalog-first RGAA 4.1.2 evidence and review workflow",
    )
    rgaa_sub = parser.add_subparsers(dest="rgaa_action", required=True)

    catalog_parser = rgaa_sub.add_parser("catalog", help="inspect the pinned 258-test catalog")
    catalog_parser.add_argument(
        "--tests", type=_tests_arg, default=None, help="comma-separated test IDs"
    )
    catalog_parser.set_defaults(func=cmd_catalog)

    scan_parser = rgaa_sub.add_parser("scan", help="scan the assigned page or navigate first")
    scan_parser.add_argument("url", nargs="?", default=None)
    scan_parser.add_argument(
        "--scope", choices=["passive", "interactive", "privileged"], default="passive"
    )
    scan_parser.add_argument("--engine", choices=["native", "hybrid"], default="native")
    scan_parser.add_argument(
        "--tests", type=_tests_arg, default=None, help="comma-separated test IDs"
    )
    scan_parser.set_defaults(func=cmd_scan)

    sample_parser = rgaa_sub.add_parser("sample", help="validate or run a declared page sample")
    sample_sub = sample_parser.add_subparsers(dest="rgaa_sample_action", required=True)
    validate_parser = sample_sub.add_parser("validate", help="compile without a browser session")
    validate_parser.add_argument("path")
    validate_parser.set_defaults(func=cmd_sample_validate)
    run_parser = sample_sub.add_parser("run", help="run every declared page and aggregate verdicts")
    run_parser.add_argument("path")
    run_parser.set_defaults(func=cmd_sample_run)
