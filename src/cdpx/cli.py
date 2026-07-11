"""CLI cdpx — interface agent/humain vers les primitives CDP.

Contrat de sortie (voir HARNESS.md):
- stdout = UN objet JSON compact par défaut (parsable machine, sobre en tokens).
- --pretty = JSON indenté pour lecture humaine.
- stderr = diagnostics humains.
- exit 0 = succès, 1 = erreur d'exécution (CDP/JS/timeout), 2 = erreur d'usage.

Connexion: --port/--host ciblent un Chrome lancé avec
  chrome --remote-debugging-port=9222 --user-data-dir=/tmp/cdpx-profile
Le target est la première page, ou --target <id> (voir `cdpx tabs list`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from cdpx import __version__, discovery, output, scenarios
from cdpx.client import CDPClient, CDPError, CDPTimeout
from cdpx.primitives import (
    actions,
    advanced,
    audit,
    capture,
    dev,
    inputs,
    js,
    nav,
    net,
    profiler_panels,
    state,
)


def _action(args) -> list[str]:
    """Action composée (REMAINDER), débarrassée du séparateur `--` initial."""
    action = getattr(args, "action", None) or []
    if not isinstance(action, list):
        return []
    return action[1:] if action and action[0] == "--" else action


def _client(args) -> CDPClient:
    target = discovery.pick_page(args.host, args.port, args.target)
    origins = os.environ.get("CDPX_ORIGINS")
    action = _action(args)
    guard_url = target.get("url")
    guard_action = action
    if args.command == "intercept" and len(action) == 2 and action[0] == "goto":
        guard_url = action[1]
    elif args.command == "vitals" and getattr(args, "click", None):
        guard_url = args.url
        guard_action = ["click", args.click]
    elif args.command == "cookies":
        guard_action = [args.action]
        if args.action == "set":
            guard_url = args.url
    # replay applique la garde après chaque goto et avant/après chaque mutation.
    if args.command != "replay":
        advanced.assert_origin_allowed(args.command, guard_url, origins, action=guard_action)
    return CDPClient(target["webSocketDebuggerUrl"], timeout=args.timeout)


def _out(args, data) -> None:
    shaped = output.bound(data, full=args.full, limit=args.limit)
    if args.pretty:
        print(json.dumps(shaped, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(shaped, ensure_ascii=False, separators=(",", ":")))


def _ndjson(data) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")), flush=True)


# -- commandes -----------------------------------------------------------------


def cmd_tabs(args) -> None:
    if args.action in {"activate", "close"} and not args.id:
        raise scenarios.ScenarioUsageError(f"tabs {args.action}: --id requis")
    if args.action != "new" and args.url is not None:
        raise scenarios.ScenarioUsageError(f"tabs {args.action}: --url non supporté")
    if args.action not in {"activate", "close"} and args.id is not None:
        raise scenarios.ScenarioUsageError(f"tabs {args.action}: --id non supporté")
    if args.action == "list":
        targets = discovery.list_targets(args.host, args.port)
        tabs = [
            {
                "id": t.get("id"),
                "type": t.get("type"),
                "title": t.get("title"),
                "url": t.get("url"),
            }
            for t in targets
        ]
        _out(
            args,
            {"tabs": tabs, "count": len(tabs)},
        )
    elif args.action == "new":
        _out(args, discovery.new_tab(args.host, args.port, args.url))
    elif args.action == "activate":
        discovery.activate_tab(args.host, args.port, args.id)
        _out(args, {"activated": args.id})
    elif args.action == "close":
        discovery.close_tab(args.host, args.port, args.id)
        _out(args, {"closed": args.id})


def cmd_version(args) -> None:
    _out(args, discovery.version(args.host, args.port))


def cmd_goto(args) -> None:
    with _client(args) as c:
        result = nav.navigate(c, args.url, wait=args.wait, timeout=args.timeout)
        _require_navigation(result)
        _out(args, result)


def cmd_wait(args) -> None:
    with _client(args) as c:
        _out(args, nav.wait_for(c, args.selector, timeout=args.timeout))


def cmd_eval(args) -> None:
    with _client(args) as c:
        value = js.evaluate(c, args.expression, await_promise=args.await_promise)
        _out(args, {"value": value})


def cmd_text(args) -> None:
    with _client(args) as c:
        _out(args, js.get_text(c, args.selector))


def cmd_html(args) -> None:
    with _client(args) as c:
        _out(args, js.get_html(c, args.selector))


def cmd_count(args) -> None:
    with _client(args) as c:
        _out(args, js.count(c, args.selector))


def cmd_click(args) -> None:
    with _client(args) as c:
        _out(args, inputs.click(c, args.selector))


def cmd_type(args) -> None:
    with _client(args) as c:
        _out(args, inputs.type_text(c, args.selector, args.text, clear=args.clear))


def cmd_key(args) -> None:
    with _client(args) as c:
        _out(args, inputs.press_key(c, args.key))


def cmd_screenshot(args) -> None:
    with _client(args) as c:
        _out(args, capture.screenshot(c, args.output, full_page=args.full_page, fmt=args.fmt))


def cmd_pdf(args) -> None:
    with _client(args) as c:
        _out(args, capture.pdf(c, args.output))


def cmd_console(args) -> None:
    with _client(args) as c:
        if args.follow:
            try:
                for entry in capture.console_follow(c, max_entries=args.max):
                    _ndjson(entry)
            except KeyboardInterrupt:
                return
        else:
            _out(args, capture.console_capture(c, duration=args.duration))


def cmd_network(args) -> None:
    with _client(args) as c:
        _out(args, net.capture(c, args.url, timeout=args.timeout, settle=args.settle))


def cmd_cookies(args) -> None:
    if args.action == "set":
        missing = [
            flag
            for flag, value in (("--name", args.name), ("--value", args.value), ("--url", args.url))
            if value is None
        ]
        if missing:
            raise scenarios.ScenarioUsageError(f"cookies set: {', '.join(missing)} requis")
        if args.show_values:
            raise scenarios.ScenarioUsageError("cookies set: --show-values non supporté")
    elif any(value is not None for value in (args.name, args.value, args.url)):
        raise scenarios.ScenarioUsageError(
            f"cookies {args.action}: --name/--value/--url non supportés"
        )
    elif args.action == "clear" and args.show_values:
        raise scenarios.ScenarioUsageError("cookies clear: --show-values non supporté")
    with _client(args) as c:
        if args.action == "get":
            _out(args, state.get_cookies(c, show_values=args.show_values))
        elif args.action == "set":
            _out(args, state.set_cookie(c, args.name, args.value, args.url))
        elif args.action == "clear":
            _out(args, state.clear_cookies(c))


def cmd_storage(args) -> None:
    with _client(args) as c:
        _out(args, state.get_storage(c, kind=args.kind))


def cmd_seo(args) -> None:
    with _client(args) as c:
        if args.url:
            _require_navigation(nav.navigate(c, args.url, wait="load", timeout=args.timeout))
        _out(args, audit.seo(c))


def cmd_metrics(args) -> None:
    with _client(args) as c:
        _out(args, audit.metrics(c))


def cmd_profiler(args) -> None:
    with _client(args) as c:
        _out(
            args,
            dev.profiler(c, args.url, timeout=args.timeout, settle=args.settle, panels=args.panels),
        )


def cmd_dom_diff(args) -> None:
    with _client(args) as c:
        _out(args, dev.dom_diff(c, _action(args)))


def cmd_intercept(args) -> None:
    action = _action(args)
    if len(action) != 2 or action[0] != "goto":
        raise ValueError("intercept supporte: -- goto <url>")
    with _client(args) as c:
        _out(
            args,
            advanced.intercept_goto(
                c,
                args.rule,
                action[1],
                timeout=args.timeout,
                settle=args.settle,
            ),
        )


def cmd_emulate(args) -> None:
    action = _action(args)
    with _client(args) as c:
        res = advanced.emulate(c, preset=args.preset, reset=args.reset)
        if action:
            # Les overrides meurent avec la connexion: agir sous émulation
            # exige d'exécuter l'action DANS cette connexion (cf. e2e).
            res["action"] = {
                "argv": action,
                "result": actions.run_action(c, action, timeout=args.timeout),
            }
        _out(args, res)


def cmd_vitals(args) -> None:
    with _client(args) as c:
        _out(
            args,
            advanced.vitals(
                c,
                args.url,
                timeout=args.timeout,
                click_selector=args.click,
                settle=args.settle,
                origins=os.environ.get("CDPX_ORIGINS"),
            ),
        )


def cmd_a11y(args) -> None:
    with _client(args) as c:
        _out(args, advanced.a11y(c))


def cmd_coverage(args) -> None:
    with _client(args) as c:
        _out(args, advanced.coverage(c, args.url, timeout=args.timeout))


def cmd_frame(args) -> None:
    with _client(args) as c:
        _out(args, advanced.frame_text(c, args.selector))


def cmd_record(args) -> None:
    with _client(args) as c:
        _out(args, advanced.record(c, args.output, _action(args)))


def cmd_replay(args) -> int:
    with _client(args) as c:
        res = advanced.replay(
            c,
            args.path,
            max_actions=args.max_actions,
            origins=os.environ.get("CDPX_ORIGINS"),
        )
    _out(args, res)
    # divergence = erreur d'exécution: JSON structuré sur stdout, exit 1
    return 0 if res.get("ok") else 1


def cmd_scenario(args) -> int:
    if args.scenario_action != "run":
        raise scenarios.ScenarioUsageError("scenario supporte: run <path>")
    scenario = scenarios.load(args.path)
    with _client(args) as c:
        res = scenarios.run(
            c,
            scenario,
            evidence_root=args.evidence_dir,
            timeout=args.timeout,
            settle=args.settle,
            origins=os.environ.get("CDPX_ORIGINS"),
        )
    _out(args, res)
    return 0 if res["verdict"] == "pass" else 1


# -- parseur ---------------------------------------------------------------------


def _panels_arg(value: str) -> list[str] | None:
    """--panels: all (défaut) -> tous, none -> sonde token seule, sinon liste CSV."""
    if value == "all":
        return None
    if value == "none":
        return []
    panels = [item.strip() for item in value.split(",") if item.strip()]
    try:
        return profiler_panels.normalize_panels(panels)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def _require_navigation(result: dict) -> None:
    if result.get("ok") is False:
        raise ValueError(f"navigation échouée: {result.get('errorText') or result.get('url')}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cdpx", description=__doc__)
    p.add_argument("--version", action="version", version=f"cdpx {__version__}")
    p.add_argument("--host", default=os.environ.get("CDPX_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("CDPX_PORT", "9222")))
    p.add_argument("--target", default=None, help="id du target (défaut: première page)")
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

    s = sub.add_parser("tabs", help="gestion des onglets")
    s.add_argument("action", choices=["list", "new", "activate", "close"])
    s.add_argument("--url", default=None)
    s.add_argument("--id", default=None)
    s.set_defaults(func=cmd_tabs)

    s = sub.add_parser("version", help="infos du navigateur")
    s.set_defaults(func=cmd_version)

    s = sub.add_parser("goto", help="naviguer vers une URL")
    s.add_argument("url")
    s.add_argument("--wait", choices=["load", "domcontentloaded", "none"], default="load")
    s.set_defaults(func=cmd_goto)

    s = sub.add_parser("wait", help="attendre un sélecteur CSS")
    s.add_argument("selector")
    s.set_defaults(func=cmd_wait)

    s = sub.add_parser("eval", help="évaluer du JS dans la page")
    s.add_argument("expression")
    s.add_argument("--await", dest="await_promise", action="store_true")
    s.set_defaults(func=cmd_eval)

    s = sub.add_parser("text", help="innerText (élément ou body)")
    s.add_argument("selector", nargs="?", default=None)
    s.set_defaults(func=cmd_text)

    s = sub.add_parser("html", help="outerHTML (élément ou document)")
    s.add_argument("selector", nargs="?", default=None)
    s.set_defaults(func=cmd_html)

    s = sub.add_parser("count", help="compter les éléments d'un sélecteur")
    s.add_argument("selector")
    s.set_defaults(func=cmd_count)

    s = sub.add_parser("click", help="cliquer un élément (Input domain)")
    s.add_argument("selector")
    s.set_defaults(func=cmd_click)

    s = sub.add_parser("type", help="taper du texte dans un champ")
    s.add_argument("selector")
    s.add_argument("text")
    s.add_argument("--clear", action="store_true", help="vider le champ avant")
    s.set_defaults(func=cmd_type)

    s = sub.add_parser("key", help="presser une touche (Enter, Tab, Escape, ArrowUp/Down)")
    s.add_argument("key")
    s.set_defaults(func=cmd_key)

    s = sub.add_parser("screenshot", help="capture d'écran PNG ou JPEG")
    s.add_argument("-o", "--output", default="screenshot.png")
    s.add_argument("--full-page", action="store_true")
    s.add_argument("--format", dest="fmt", choices=["png", "jpeg"], default="png")
    s.set_defaults(func=cmd_screenshot)

    s = sub.add_parser("pdf", help="imprimer la page en PDF")
    s.add_argument("-o", "--output", default="page.pdf")
    s.set_defaults(func=cmd_pdf)

    s = sub.add_parser("console", help="capturer logs et exceptions JS")
    s.add_argument("--duration", type=float, default=2.0)
    s.add_argument("--follow", action="store_true", help="stream NDJSON jusqu'à Ctrl-C ou --max")
    s.add_argument("--max", type=int, default=None, help="nombre max d'entrées en mode follow")
    s.set_defaults(func=cmd_console)

    s = sub.add_parser("network", help="naviguer en capturant l'activité réseau")
    s.add_argument("url")
    s.add_argument("--settle", type=float, default=0.5)
    s.set_defaults(func=cmd_network)

    s = sub.add_parser("cookies", help="cookies (valeurs masquées par défaut)")
    s.add_argument("action", choices=["get", "set", "clear"])
    s.add_argument("--show-values", action="store_true")
    s.add_argument("--name", default=None)
    s.add_argument("--value", default=None)
    s.add_argument("--url", default=None)
    s.set_defaults(func=cmd_cookies)

    s = sub.add_parser("storage", help="localStorage / sessionStorage")
    s.add_argument("--kind", choices=["local", "session"], default="local")
    s.set_defaults(func=cmd_storage)

    s = sub.add_parser("seo", help="audit SEO on-page du DOM rendu")
    s.add_argument("url", nargs="?", default=None, help="naviguer d'abord (optionnel)")
    s.set_defaults(func=cmd_seo)

    s = sub.add_parser("metrics", help="métriques de performance du renderer")
    s.set_defaults(func=cmd_metrics)

    s = sub.add_parser("profiler", help="parser les panels du Web Profiler Symfony")
    s.add_argument("url")
    s.add_argument("--settle", type=float, default=0.2)
    s.add_argument(
        "--panels",
        type=_panels_arg,
        default="all",
        help=f"all | none | liste: {','.join(profiler_panels.ALL_PANELS)}",
    )
    s.set_defaults(func=cmd_profiler)

    s = sub.add_parser("dom-diff", help="diff DOM stable autour d'une action")
    s.add_argument("action", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_dom_diff)

    s = sub.add_parser("intercept", help="intercepter des requêtes pendant une commande")
    s.add_argument("--rule", action="append", required=True, help="PATTERN => 503|block|continue")
    s.add_argument("--settle", type=float, default=0.5)
    s.add_argument("action", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_intercept)

    s = sub.add_parser("emulate", help="émulation mobile/réseau/CPU (+ action composée)")
    s.add_argument("preset", nargs="?", choices=["mobile", "slow-3g", "cpu-4x"])
    s.add_argument("--reset", action="store_true")
    s.add_argument("action", nargs=argparse.REMAINDER, help="-- goto <url> | click <sel> | ...")
    s.set_defaults(func=cmd_emulate)

    s = sub.add_parser("vitals", help="Core Web Vitals basiques")
    s.add_argument("url")
    s.add_argument("--click", default=None, help="sélecteur à cliquer pour mesurer INP")
    s.add_argument("--settle", type=float, default=0.5)
    s.set_defaults(func=cmd_vitals)

    s = sub.add_parser("a11y", help="arbre d'accessibilité compact")
    s.set_defaults(func=cmd_a11y)

    s = sub.add_parser("coverage", help="coverage JS par fichier")
    s.add_argument("url")
    s.set_defaults(func=cmd_coverage)

    s = sub.add_parser("frame", help="lire du texte dans une iframe")
    s.add_argument("selector")
    s.set_defaults(func=cmd_frame)

    s = sub.add_parser("record", help="exécuter une action et la journaliser en NDJSON")
    s.add_argument("-o", "--output", default="cdpx-record.ndjson")
    s.add_argument("action", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_record)

    s = sub.add_parser("replay", help="rejouer un journal NDJSON, stop à la divergence")
    s.add_argument("path")
    s.set_defaults(func=cmd_replay)

    s = sub.add_parser("scenario", help="exécuter un scénario métier déclaratif")
    scenario_sub = s.add_subparsers(dest="scenario_action", required=True)
    r = scenario_sub.add_parser("run", help="exécuter un fichier scénario YAML")
    r.add_argument("path")
    r.add_argument("--evidence-dir", default=".cdpx-evidence")
    r.add_argument("--settle", type=float, default=0.5)
    r.set_defaults(func=cmd_scenario)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code = args.func(args)
        return code or 0
    except scenarios.ScenarioUsageError as e:
        print(f"cdpx: {e}", file=sys.stderr)
        return 2
    except (
        CDPError,
        CDPTimeout,
        discovery.DiscoveryError,
        js.JSException,
        inputs.ElementNotFound,
        ValueError,
        TimeoutError,
    ) as e:
        print(f"cdpx: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
