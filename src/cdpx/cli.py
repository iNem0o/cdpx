"""CLI cdpx — interface agent/humain vers les primitives CDP.

Contrat de sortie (voir HARNESS.md):
- stdout = UN objet JSON compact par défaut (parsable machine, sobre en tokens).
- --pretty = JSON indenté pour lecture humaine.
- stderr = diagnostics humains.
- exit 0 = succès, 1 = erreur d'exécution (CDP/JS/timeout), 2 = erreur d'usage.

Connexion: chaque commande navigateur utilise un manifest de session supervisée,
un run et un target explicitement attribués.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cdpx import __version__, discovery, output, scenarios, session
from cdpx.cli_context import CommandInvocation, CommandOptions
from cdpx.client import CDPError, CDPTimeout, CDPTransportError
from cdpx.commands.diagnostics import register_commands as register_diagnostic_commands
from cdpx.commands.navigation import register_commands as register_page_commands
from cdpx.commands.orchestration import register_commands as register_orchestration_commands
from cdpx.commands.sessions import register_commands as register_session_commands
from cdpx.commands.shared import (
    build_redaction_context as _build_redaction_context,
)
from cdpx.commands.state import register_commands as register_state_commands
from cdpx.policy import (
    Authority,
    PolicyError,
)
from cdpx.primitives import (
    inputs,
    js,
)
from cdpx.security import redact_text

# -- commandes -----------------------------------------------------------------
# -- parseur ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cdpx", description=__doc__)
    p.add_argument("--version", action="version", version=f"cdpx {__version__}")
    p.add_argument("--target", default=None, help="id du target attribué (CDPX_TARGET)")
    p.add_argument("--session", default=None, help="manifest de session (CDPX_SESSION)")
    p.add_argument("--run-id", default=None, help="run propriétaire (CDPX_RUN_ID)")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--pretty", action="store_true", help="JSON indenté pour lecture humaine")
    p.add_argument("--full", action="store_true", help="ne pas borner les sorties volumineuses")
    p.add_argument("--max-actions", type=int, default=None, help="budget d'actions agentiques")
    p.add_argument(
        "--limit",
        type=int,
        default=output.DEFAULT_LIMIT,
        help="nombre max d'items par liste volumineuse (défaut: 50)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    register_page_commands(sub)
    register_state_commands(sub)
    register_diagnostic_commands(sub)
    register_orchestration_commands(sub)
    register_session_commands(sub)

    return p


def _argument_or_environment(value: str | None, name: str) -> str | None:
    return value if value is not None else os.environ.get(name)


def _require_session_values(values: tuple[tuple[str, str | None], ...]) -> None:
    missing = [label for label, value in values if not value]
    if missing:
        raise scenarios.ScenarioUsageError(
            f"session: {', '.join(missing)} requis via argument ou environnement"
        )


def _prepare_args(args: CommandInvocation) -> CommandInvocation:
    if args.options.command == "session":
        if (
            args.options.session is not None
            or args.options.run_id is not None
            or args.options.target is not None
        ):
            raise scenarios.ScenarioUsageError(
                "session start/status/stop utilise ses options propres après la sous-commande"
            )
        args = args.with_session_run_id(
            _argument_or_environment(
                args.options.session_run_id,
                "CDPX_RUN_ID",
            )
        )
        if args.options.session_action == "start":
            _require_session_values((("--run-id/CDPX_RUN_ID", args.options.session_run_id),))
            if args.options.full and args.options.authority is not Authority.PRIVILEGED:
                raise PolicyError("session: --full requiert privileged")
            return args
        args = args.with_lifecycle_identity(
            path=_argument_or_environment(args.options.session_path, "CDPX_SESSION"),
            target=_argument_or_environment(args.options.session_target, "CDPX_TARGET"),
        )
        _require_session_values(
            (
                ("--session/CDPX_SESSION", args.options.session_path),
                ("--run-id/CDPX_RUN_ID", args.options.session_run_id),
                ("--target/CDPX_TARGET", args.options.session_target),
            )
        )
        session_path = args.options.session_path
        run_id = args.options.session_run_id
        target_id = args.options.session_target
        if session_path is None or run_id is None or target_id is None:
            raise RuntimeError("identité de session non préparée")
        manifest = session.load_manifest(
            session_path,
            run_id=run_id,
            target_id=target_id,
        )
        if args.options.full and manifest.authority != "privileged":
            raise PolicyError("session: --full requiert privileged")
        return args.with_session(manifest)

    args = args.with_browser_identity(
        session_path=_argument_or_environment(args.options.session, "CDPX_SESSION"),
        run_id=_argument_or_environment(args.options.run_id, "CDPX_RUN_ID"),
        target=_argument_or_environment(args.options.target, "CDPX_TARGET"),
    )
    _require_session_values(
        (
            ("--session/CDPX_SESSION", args.options.session),
            ("--run-id/CDPX_RUN_ID", args.options.run_id),
            ("--target/CDPX_TARGET", args.options.target),
        )
    )
    session_path = args.options.session
    run_id = args.options.run_id
    target_id = args.options.target
    if session_path is None or run_id is None or target_id is None:
        raise RuntimeError("identité de session non préparée")
    manifest = session.load_manifest(session_path, run_id=run_id, target_id=target_id)
    session.assert_session_active(manifest)
    evidence_dir = (
        str(Path(manifest.artifacts_dir) / "scenarios")
        if args.options.command == "scenario"
        else ""
    )
    if args.options.full and manifest.authority != "privileged":
        raise PolicyError("session: --full requiert privileged")
    return args.with_runtime_endpoint(
        host=manifest.host,
        port=manifest.port,
        evidence_dir=evidence_dir,
    ).with_session(manifest)


def _error_text(args: CommandInvocation, error: Exception) -> str:
    return redact_text(str(error), context=args.redaction)


def main(argv: list[str] | None = None) -> int:
    options = CommandOptions.from_namespace(build_parser().parse_args(argv))
    args = CommandInvocation(options=options, redaction=_build_redaction_context(options))
    try:
        args = _prepare_args(args)
        code = args.options.func(args)
        return code or 0
    except scenarios.ScenarioUsageError as e:
        print(f"cdpx: {_error_text(args, e)}", file=sys.stderr)
        return 2
    except (
        CDPError,
        CDPTimeout,
        CDPTransportError,
        discovery.DiscoveryError,
        js.JSException,
        inputs.ElementNotFound,
        ValueError,
        TimeoutError,
    ) as e:
        print(f"cdpx: {_error_text(args, e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
