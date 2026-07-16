"""CLI handlers for recorded and declarative browser orchestration."""

from __future__ import annotations

import argparse

from cdpx import scenarios
from cdpx.cli_context import CommandInvocation
from cdpx.commands.shared import (
    artifact_metadata,
    artifact_path,
    artifact_ttl,
    browser_client,
    emit_json,
    execution,
    orchestration,
    preflight_actions,
    preflight_replay,
    preflight_scenario,
    require_action,
)
from cdpx.policy import PolicyError
from cdpx.primitives import recording


def register_commands(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = sub.add_parser("record", help="exécuter une action et la journaliser en NDJSON")
    parser.add_argument("-o", "--output", default="cdpx-record.ndjson")
    parser.add_argument("action", nargs=argparse.REMAINDER)
    parser.set_defaults(func=cmd_record)
    parser = sub.add_parser("replay", help="rejouer un journal NDJSON, stop à la divergence")
    parser.add_argument("path")
    parser.set_defaults(func=cmd_replay)
    parser = sub.add_parser("scenario", help="exécuter un scénario métier déclaratif")
    scenario_sub = parser.add_subparsers(dest="scenario_action", required=True)
    run = scenario_sub.add_parser("run", help="exécuter un fichier scénario YAML")
    run.add_argument("path")
    run.add_argument("--settle", type=float, default=0.5)
    run.set_defaults(func=cmd_scenario)


def cmd_record(args: CommandInvocation) -> None:
    action = require_action(args)
    required = preflight_actions(args, [action])
    output = args.options.output
    if output is None:
        raise RuntimeError("sortie de journal non préparée")
    with browser_client(args, required_authority=required) as client:
        path = artifact_path(args, output, "journals")
        result = recording.record(
            client,
            path,
            action,
            run_id=execution(args).run_id,
            context=orchestration(args),
        )
    emit_json(args, artifact_metadata(args, result, "internal"))


def cmd_replay(args: CommandInvocation) -> int:
    requested_path = args.options.path
    if requested_path is None:
        raise RuntimeError("journal à rejouer non préparé")
    path = artifact_path(args, requested_path, "journals", must_exist=True)
    required = preflight_replay(args, path)
    with browser_client(args, required_authority=required) as client:
        result = recording.replay(
            client,
            path,
            max_actions=args.options.max_actions,
            context=orchestration(args),
        )
    emit_json(args, result)
    return 0 if result.get("ok") else 1


def cmd_scenario(args: CommandInvocation) -> int:
    if args.options.scenario_action != "run":
        raise scenarios.ScenarioUsageError("scenario supporte: run <path>")
    path = args.options.path
    if path is None:
        raise RuntimeError("scénario à exécuter non préparé")
    scenario = scenarios.load(path)
    try:
        prepared = scenarios.prepare(scenario, orchestration(args))
    except scenarios.ScenarioUsageError as error:
        raise PolicyError(str(error)) from error
    required = preflight_scenario(args, prepared)
    with browser_client(args, required_authority=required) as client:
        result = scenarios.run(
            client,
            prepared,
            evidence_root=args.options.evidence_dir,
            timeout=args.options.timeout,
            settle=args.options.settle,
            run_id=execution(args).run_id,
            artifact_ttl=artifact_ttl(args),
        )
    emit_json(args, result)
    return 0 if result["verdict"] == "pass" else 1
