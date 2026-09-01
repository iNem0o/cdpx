"""CLI surface for catalog-first RGAA scans and declared page samples."""

from __future__ import annotations

import argparse
from typing import cast

from cdpx import scenarios
from cdpx.client import CDPError, CDPTimeout, CDPTransportError
from cdpx.commands.shared import (
    assert_session_current,
    browser_client,
    emit_rgaa_json,
    execution,
    verified_session_url,
)
from cdpx.policy import assert_url_allowed
from cdpx.primitives import nav
from cdpx.rgaa.catalog import CatalogError, describe_catalog, parse_test_selection, test_index
from cdpx.rgaa.plan import ExecutionBudget, build_scan_plan
from cdpx.rgaa.sample import compile_sample, finalize_sample_report_error, run_sample
from cdpx.rgaa.scanner import Engine, Scope, finalize_report_error, scan, scan_error_report


def _tests_arg(value: str) -> tuple[str, ...]:
    try:
        selected = parse_test_selection(value)
    except CatalogError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    if not selected:
        raise argparse.ArgumentTypeError("RGAA test selection must not be empty")
    return selected


def cmd_catalog(args) -> None:
    emit_rgaa_json(args, describe_catalog(args.options.tests))


def cmd_scan(args) -> int:
    selected = args.options.tests
    context = execution(args)
    if args.options.url:
        assert_url_allowed(args.options.url, context.origins)
    wanted = set(selected or test_index())
    plan = build_scan_plan(wanted, scope=args.options.scope, engine=args.options.engine)
    required = plan.required_authority
    maximum = plan.maximum_actions + (1 if args.options.url else 0)
    if args.options.max_actions is not None and maximum > args.options.max_actions:
        raise scenarios.ScenarioUsageError(
            f"--max-actions budget too small for RGAA plan: {maximum} required"
        )
    budget = ExecutionBudget.start(args.options.timeout, args.options.max_actions)
    with browser_client(args, required_authority=required) as client:
        if args.options.url:
            budget.consume("RGAA navigation")
            try:
                nav.navigate(client, args.options.url, wait="load", timeout=budget.remaining())
            except (nav.NavigationError, CDPError, CDPTimeout, CDPTransportError) as error:
                emit_rgaa_json(
                    args,
                    scan_error_report(
                        scope=cast(Scope, args.options.scope),
                        engine=cast(Engine, args.options.engine),
                        selected_tests=selected,
                        error=error,
                        budget=budget,
                        planned_navigations=1,
                    ),
                )
                return 1
        try:
            verified_url = verified_session_url(args, client, timeout=budget.remaining())
        except (CDPError, CDPTimeout, CDPTransportError, TimeoutError) as error:
            emit_rgaa_json(
                args,
                scan_error_report(
                    scope=cast(Scope, args.options.scope),
                    engine=cast(Engine, args.options.engine),
                    selected_tests=selected,
                    error=error,
                    budget=budget,
                    planned_navigations=1 if args.options.url else 0,
                    collector="initial-document-verification",
                ),
            )
            return 1
        result = scan(
            client,
            scope=cast(Scope, args.options.scope),
            engine=cast(Engine, args.options.engine),
            selected_tests=selected,
            timeout=args.options.timeout,
            budget=budget,
            origin_guard=lambda remaining: verified_session_url(args, client, timeout=remaining),
            planned_navigations=1 if args.options.url else 0,
            document_url=verified_url,
        )
        try:
            assert_session_current(args, client, timeout=budget.remaining())
        except (CDPError, CDPTimeout, CDPTransportError, TimeoutError) as error:
            finalize_report_error(result, error)
        emit_rgaa_json(args, result)
        return 0 if result["execution_status"] == "complete" else 1


def _compile_or_usage(path: str | None):
    if path is None:
        raise scenarios.ScenarioUsageError("RGAA sample path required")
    try:
        return compile_sample(path)
    except ValueError as error:
        raise scenarios.ScenarioUsageError(str(error)) from error


def cmd_sample_validate(args) -> None:
    compiled = _compile_or_usage(args.options.path)
    emit_rgaa_json(args, compiled.public_plan())


def cmd_sample_run(args) -> int:
    compiled = _compile_or_usage(args.options.path)
    context = execution(args)
    for page in compiled.pages:
        assert_url_allowed(page.url, context.origins)
    maximum = compiled.public_plan()["planned_actions"]["total"]
    if args.options.max_actions is not None and maximum > args.options.max_actions:
        raise scenarios.ScenarioUsageError(
            f"--max-actions budget too small for RGAA sample plan: {maximum} required"
        )
    budget = ExecutionBudget.start(args.options.timeout, args.options.max_actions)
    with browser_client(args, required_authority=compiled.authority) as client:
        result = run_sample(
            client,
            compiled,
            timeout=args.options.timeout,
            budget=budget,
            origin_guard=lambda remaining: verified_session_url(args, client, timeout=remaining),
        )
        try:
            assert_session_current(args, client, timeout=budget.remaining())
        except (CDPError, CDPTimeout, CDPTransportError, TimeoutError) as error:
            finalize_sample_report_error(result, error)
        emit_rgaa_json(args, result)
        return 0 if result["execution_status"] == "complete" else 1


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
