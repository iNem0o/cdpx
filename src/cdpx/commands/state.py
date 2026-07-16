"""Session state, network, console, and capture CLI command family."""

from __future__ import annotations

import argparse

from cdpx import scenarios
from cdpx.commands.artifacts import cmd_pdf, cmd_screenshot
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
    emit_ndjson as _ndjson,
)
from cdpx.commands.shared import (
    resolve_sensitive_value as _resolve_sensitive_value,
)
from cdpx.primitives import capture, net, state


def cmd_console(args) -> None:
    with _client(args) as c:
        if args.options.follow:
            try:
                for entry in capture.console_follow(
                    c,
                    max_entries=args.options.max,
                    context=args.redaction,
                ):
                    _assert_session_current(args, c)
                    _ndjson(args, entry)
                _assert_session_current(args, c)
            except KeyboardInterrupt:
                return
        else:
            result = capture.console_capture(
                c,
                duration=args.options.duration,
                context=args.redaction,
            )
            _assert_session_current(args, c)
            _out(
                args,
                result,
            )


def cmd_network(args) -> None:
    with _client(args) as c:
        result = net.capture(
            c,
            args.options.url,
            timeout=args.options.timeout,
            settle=args.options.settle,
            context=args.redaction,
        )
        _assert_session_current(args, c)
        _out(args, result)


def cmd_cookies(args) -> None:
    if args.options.action == "set":
        value = _resolve_sensitive_value(
            args,
            literal=None,
            env_name=args.options.value_env,
            label="cookies set",
        )
        missing = [
            flag
            for flag, item in (("--name", args.options.name), ("--url", args.options.url))
            if item is None
        ]
        if missing:
            raise scenarios.ScenarioUsageError(f"cookies set: {', '.join(missing)} requis")
        if args.options.show_values:
            raise scenarios.ScenarioUsageError("cookies set: --show-values non supporté")
    elif any(
        value is not None for value in (args.options.name, args.options.value_env, args.options.url)
    ):
        raise scenarios.ScenarioUsageError(
            f"cookies {args.options.action}: --name/--value/--url non supportés"
        )
    elif args.options.action == "clear" and args.options.show_values:
        raise scenarios.ScenarioUsageError("cookies clear: --show-values non supporté")
    with _client(args) as c:
        if args.options.action == "get":
            _out(args, state.get_cookies(c, show_values=args.options.show_values))
        elif args.options.action == "set":
            _out(args, state.set_cookie(c, args.options.name, value, args.options.url))
        elif args.options.action == "clear":
            _out(args, state.clear_cookies(c))


def cmd_storage(args) -> None:
    with _client(args) as c:
        result = state.get_storage(c, kind=args.options.kind, show_values=args.options.show_values)
        _assert_session_current(args, c)
        _out(args, result)


def register_commands(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = sub.add_parser("screenshot", help="capture d'écran PNG ou JPEG")
    parser.add_argument("-o", "--output", default="screenshot.png")
    parser.add_argument("--full-page", action="store_true")
    parser.add_argument("--format", dest="fmt", choices=["png", "jpeg"], default="png")
    parser.set_defaults(func=cmd_screenshot)

    parser = sub.add_parser("pdf", help="imprimer la page en PDF")
    parser.add_argument("-o", "--output", default="page.pdf")
    parser.set_defaults(func=cmd_pdf)

    parser = sub.add_parser("console", help="capturer logs et exceptions JS")
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument(
        "--follow", action="store_true", help="stream NDJSON jusqu'à Ctrl-C ou --max"
    )
    parser.add_argument("--max", type=int, default=None, help="nombre max d'entrées en mode follow")
    parser.set_defaults(func=cmd_console)

    parser = sub.add_parser("network", help="naviguer en capturant l'activité réseau")
    parser.add_argument("url")
    parser.add_argument("--settle", type=float, default=0.5)
    parser.set_defaults(func=cmd_network)

    parser = sub.add_parser("cookies", help="cookies (valeurs masquées par défaut)")
    parser.add_argument("action", choices=["get", "set", "clear"])
    parser.add_argument("--show-values", action="store_true")
    parser.add_argument("--name", default=None)
    parser.add_argument("--value-env", default=None, help="lire la valeur depuis cette variable")
    parser.add_argument("--url", default=None)
    parser.set_defaults(func=cmd_cookies)

    parser = sub.add_parser("storage", help="localStorage / sessionStorage")
    parser.add_argument("--kind", choices=["local", "session"], default="local")
    parser.add_argument("--show-values", action="store_true")
    parser.set_defaults(func=cmd_storage)
