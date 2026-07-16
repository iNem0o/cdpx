"""Diagnostics, audits, emulation, and interception CLI command family."""

from __future__ import annotations

import argparse

from cdpx.action_model import ClickAction, GotoAction, action_argv
from cdpx.commands.shared import (
    action as _action,
)
from cdpx.commands.shared import (
    assert_session_current as _assert_session_current,
)
from cdpx.commands.shared import (
    browser_client as _client,
)
from cdpx.commands.shared import (
    emit_json as _out,
)
from cdpx.commands.shared import (
    orchestration as _orchestration,
)
from cdpx.commands.shared import (
    origins as _origins,
)
from cdpx.commands.shared import (
    require_action as _require_action,
)
from cdpx.policy import action_authority
from cdpx.primitives import (
    actions,
    audit,
    dev,
    diagnostics,
    emulation,
    frames,
    interception,
    nav,
    profiler,
)


def cmd_seo(args) -> None:
    with _client(args) as c:
        if args.options.url:
            nav.navigate(c, args.options.url, wait="load", timeout=args.options.timeout)
            _assert_session_current(args, c)
        result = audit.seo(c)
        _assert_session_current(args, c)
        _out(args, result)


def cmd_metrics(args) -> None:
    with _client(args) as c:
        result = audit.metrics(c)
        _assert_session_current(args, c)
        _out(args, result)


def cmd_profiler(args) -> None:
    with _client(args) as c:
        result = dev.profiler(
            c,
            args.options.url,
            timeout=args.options.timeout,
            settle=args.options.settle,
            panels=args.options.panels,
            context=_orchestration(args),
        )
        _assert_session_current(args, c)
        _out(args, result)


def cmd_dom_diff(args) -> None:
    action = _require_action(args)
    with _client(args, required_authority=action_authority(action)) as c:
        result = dev.dom_diff(c, action)
        _assert_session_current(args, c)
        _out(args, result)


def cmd_intercept(args) -> None:
    action = _require_action(args)
    if not isinstance(action, GotoAction):
        raise ValueError("intercept supporte: -- goto <url>")
    with _client(args) as c:
        result = interception.intercept_goto(
            c,
            action.url,
            rules=args.options.rule,
            timeout=args.options.timeout,
            settle=args.options.settle,
        )
        _assert_session_current(args, c)
        _out(args, result)


def cmd_emulate(args) -> None:
    action = _action(args)
    with _client(args) as c:
        res = emulation.emulate(c, preset=args.options.preset, reset=args.options.reset)
        if action:
            # Les overrides meurent avec la connexion: agir sous émulation
            # exige d'exécuter l'action DANS cette connexion (cf. e2e).
            res["action"] = {
                "argv": action_argv(action),
                "result": actions.run_action(c, action, timeout=args.options.timeout),
            }
            _assert_session_current(args, c)
        _out(args, res)


def cmd_vitals(args) -> None:
    required = action_authority(ClickAction(args.options.click)) if args.options.click else None
    with _client(args, required_authority=required) as c:
        result = diagnostics.vitals(
            c,
            args.options.url,
            timeout=args.options.timeout,
            click_selector=args.options.click,
            settle=args.options.settle,
            origins=_origins(args),
        )
        _assert_session_current(args, c)
        _out(args, result)


def cmd_a11y(args) -> None:
    with _client(args) as c:
        result = diagnostics.a11y(c)
        _assert_session_current(args, c)
        _out(args, result)


def cmd_coverage(args) -> None:
    with _client(args) as c:
        result = diagnostics.coverage(c, args.options.url, timeout=args.options.timeout)
        _assert_session_current(args, c)
        _out(args, result)


def cmd_frame(args) -> None:
    with _client(args) as c:
        result = frames.frame_text(c, args.options.selector)
        _assert_session_current(args, c)
        _out(args, result)


def _panels_arg(value: str) -> list[str] | None:
    """--panels: all (défaut) -> tous, none -> sonde token seule, sinon liste CSV."""
    if value == "all":
        return None
    if value == "none":
        return []
    panels = [item.strip() for item in value.split(",") if item.strip()]
    try:
        return profiler.normalize_panels(panels)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def _intercept_rule_arg(value: str) -> str:
    try:
        interception.parse_intercept_rule(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e
    return value


def register_commands(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = sub.add_parser("seo", help="audit SEO on-page du DOM rendu")
    parser.add_argument("url", nargs="?", default=None, help="naviguer d'abord (optionnel)")
    parser.set_defaults(func=cmd_seo)

    sub.add_parser("metrics", help="métriques de performance du renderer").set_defaults(
        func=cmd_metrics
    )

    parser = sub.add_parser("profiler", help="parser les panels du Web Profiler Symfony")
    parser.add_argument("url")
    parser.add_argument("--settle", type=float, default=0.2)
    parser.add_argument(
        "--panels",
        type=_panels_arg,
        default="all",
        help=f"all | none | liste: {','.join(profiler.ALL_PANELS)}",
    )
    parser.set_defaults(func=cmd_profiler)

    parser = sub.add_parser("dom-diff", help="diff DOM stable autour d'une action")
    parser.add_argument("action", nargs=argparse.REMAINDER)
    parser.set_defaults(func=cmd_dom_diff)

    parser = sub.add_parser("intercept", help="intercepter des requêtes pendant une commande")
    parser.add_argument(
        "--rule",
        action="append",
        required=True,
        type=_intercept_rule_arg,
        help="PATTERN => 200..599|block|continue",
    )
    parser.add_argument("--settle", type=float, default=0.5)
    parser.add_argument("action", nargs=argparse.REMAINDER)
    parser.set_defaults(func=cmd_intercept)

    parser = sub.add_parser("emulate", help="émulation mobile/réseau/CPU (+ action composée)")
    parser.add_argument("preset", nargs="?", choices=["mobile", "slow-3g", "cpu-4x"])
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "action", nargs=argparse.REMAINDER, help="-- goto <url> | click <sel> | ..."
    )
    parser.set_defaults(func=cmd_emulate)

    parser = sub.add_parser("vitals", help="Core Web Vitals basiques")
    parser.add_argument("url")
    parser.add_argument("--click", default=None, help="sélecteur à cliquer pour mesurer INP")
    parser.add_argument("--settle", type=float, default=0.5)
    parser.set_defaults(func=cmd_vitals)

    sub.add_parser("a11y", help="arbre d'accessibilité compact").set_defaults(func=cmd_a11y)
    parser = sub.add_parser("coverage", help="coverage JS par fichier")
    parser.add_argument("url")
    parser.set_defaults(func=cmd_coverage)
    parser = sub.add_parser("frame", help="lire du texte dans une iframe")
    parser.add_argument("selector")
    parser.set_defaults(func=cmd_frame)
