"""CLI handlers for the supervised browser session lifecycle."""

from __future__ import annotations

import argparse
import os

from cdpx import scenarios, session
from cdpx.cli_context import CommandInvocation
from cdpx.commands.shared import emit_json
from cdpx.security import redact_text


def register_commands(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = sub.add_parser("session", help="profil Chrome jetable et exclusif pour un run")
    session_sub = parser.add_subparsers(dest="session_action", required=True)
    start = session_sub.add_parser("start", help="démarrer une session Chrome supervisée")
    start.add_argument("--run-id", dest="session_run_id", default=None)
    start.add_argument(
        "--authority",
        choices=["observation", "interaction", "privileged"],
        required=True,
    )
    start.add_argument("--origins", default=os.environ.get("CDPX_ORIGINS", ""))
    start.add_argument("--ttl", type=float, default=3600.0)
    start.add_argument("--owner-pid", type=int, default=None)
    start.add_argument("--chrome", default=None)
    start.add_argument(
        "--export",
        action="store_true",
        help="émettre des lignes `export` eval-ables au lieu du JSON de démarrage",
    )
    start.add_argument(
        "--startup-timeout",
        type=float,
        default=session.DEFAULT_STARTUP_TIMEOUT,
        help=(
            "budget total du cold start Chrome en secondes "
            f"(défaut: {session.DEFAULT_STARTUP_TIMEOUT:g}, "
            f"maximum: {session.MAX_STARTUP_TIMEOUT:g})"
        ),
    )
    start.set_defaults(func=cmd_session)
    for action_name in ("status", "stop"):
        child = session_sub.add_parser(action_name, help=f"{action_name} une session gérée")
        child.add_argument("--session", dest="session_path", default=None)
        child.add_argument("--run-id", dest="session_run_id", default=None)
        child.add_argument("--target", dest="session_target", default=None)
        child.set_defaults(func=cmd_session)


def cmd_session(args: CommandInvocation) -> None:
    if args.options.session_action == "start":
        run_id = args.options.session_run_id
        authority = args.options.authority
        if run_id is None or authority is None:
            raise RuntimeError("identité de démarrage de session non préparée")
        manifest, path = session.start_session(
            run_id=run_id,
            authority=authority,
            origins=args.options.origins,
            ttl=args.options.ttl,
            owner_pid=args.options.owner_pid,
            chrome_bin=args.options.chrome,
            timeout=args.options.startup_timeout,
        )
        started = args.with_session(manifest)
        if args.options.export:
            # exception documentée au contrat stdout-JSON: lignes eval-ables
            for line in session.export_lines(manifest, path):
                print(redact_text(line, context=started.redaction))
            return
        emit_json(
            started,
            {**manifest.public_dict(), "manifest": str(path), "started": True},
        )
        return
    if args.options.session_action == "status":
        session_path = args.options.session_path
        run_id = args.options.session_run_id
        target_id = args.options.session_target
        if session_path is None or run_id is None or target_id is None:
            raise RuntimeError("identité de session non préparée")
        emit_json(
            args,
            session.session_status(
                session_path,
                run_id=run_id,
                target_id=target_id,
            ),
        )
        return
    if args.options.session_action == "stop":
        session_path = args.options.session_path
        run_id = args.options.session_run_id
        target_id = args.options.session_target
        if session_path is None or run_id is None or target_id is None:
            raise RuntimeError("identité de session non préparée")
        emit_json(
            args,
            session.stop_session(
                session_path,
                run_id=run_id,
                target_id=target_id,
                timeout=args.options.timeout,
            ),
        )
        return
    raise scenarios.ScenarioUsageError("session supporte: start, status, stop")
