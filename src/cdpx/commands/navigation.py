"""Page navigation, DOM, and input CLI command family."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any, cast

from cdpx import discovery, scenarios, session
from cdpx.client import CDPClient
from cdpx.commands.shared import (
    assert_session_current as _assert_session_current,
)
from cdpx.commands.shared import (
    browser_client as _client,
)
from cdpx.commands.shared import (
    current_http_url as _current_http_url,
)
from cdpx.commands.shared import (
    emit_json as _out,
)
from cdpx.commands.shared import (
    execution as _execution,
)
from cdpx.commands.shared import (
    resolve_sensitive_value as _resolve_sensitive_value,
)
from cdpx.policy import (
    PolicyError,
    assert_authorized,
    assert_url_allowed,
)
from cdpx.primitives import inputs, js, nav

MAX_EVAL_SOURCE_BYTES = 1_000_000


def cmd_tabs(args) -> None:
    context = _execution(args)
    assert_authorized(context, "tabs")
    with session.SessionLease(
        args.options.session, run_id=args.options.run_id, target_id=args.options.target
    ) as manifest:
        targets = discovery.list_targets(args.options.host, args.options.port)
        assigned = [target for target in targets if target.get("id") == context.target_id]
        if len(assigned) != 1:
            raise PolicyError("session: unique assigned target not found")
        target = session.assert_manifest_target_binding(manifest, assigned[0])
        with CDPClient(target["webSocketDebuggerUrl"], timeout=args.options.timeout) as client:
            current_url = _current_http_url(client)
            assert_url_allowed(current_url, context.origins)
        tab = _public_target({**target, "url": current_url})
        _out(args, {"tabs": [tab], "count": 1})


def _public_target(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": target.get("id"),
        "type": target.get("type"),
        "title": target.get("title"),
        "url": target.get("url"),
    }


def cmd_version(args) -> None:
    context = _execution(args)
    assert_authorized(context, "version")
    with session.SessionLease(
        args.options.session, run_id=args.options.run_id, target_id=args.options.target
    ):
        data = discovery.version(args.options.host, args.options.port)
    data.pop("webSocketDebuggerUrl", None)
    _out(args, data)


def cmd_goto(args) -> None:
    with _client(args) as c:
        result = nav.navigate(
            c, args.options.url, wait=args.options.wait, timeout=args.options.timeout
        )
        _assert_session_current(args, c)
        _out(args, result)


def cmd_wait(args) -> None:
    with _client(args) as c:
        result = nav.wait_for(c, args.options.selector, timeout=args.options.timeout)
        _assert_session_current(args, c)
        _out(args, result)


def cmd_eval(args) -> None:
    expression, source = _eval_source(args)
    with _client(args) as c:
        value = js.evaluate(c, expression, await_promise=args.options.await_promise)
        _assert_session_current(args, c)
        _out(
            args,
            {
                "value": value,
                "source": source,
                "script_sha256": hashlib.sha256(expression.encode()).hexdigest(),
            },
        )


def _eval_source(args) -> tuple[str, str]:
    if args.options.expression is not None:
        expression = args.options.expression
        source = "inline"
    elif args.options.expression_file is not None:
        source = "file"
        try:
            with Path(args.options.expression_file).open("rb") as handle:
                payload = handle.read(MAX_EVAL_SOURCE_BYTES + 1)
        except OSError as error:
            raise scenarios.ScenarioUsageError(f"eval: cannot read --file: {error}") from error
        if len(payload) > MAX_EVAL_SOURCE_BYTES:
            raise scenarios.ScenarioUsageError(
                f"eval: source exceeds {MAX_EVAL_SOURCE_BYTES} bytes"
            )
        try:
            expression = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise scenarios.ScenarioUsageError("eval: --file must be UTF-8") from error
    elif args.options.expression_stdin:
        source = "stdin"
        # Read raw bytes: a character-based read would let multi-byte UTF-8
        # input consume several megabytes before the byte budget is checked,
        # and the digest must depend on the original bytes, not on Python's
        # newline translation.
        try:
            stdin_buffer = getattr(sys.stdin, "buffer", sys.stdin)
            payload = cast(bytes, stdin_buffer.read(MAX_EVAL_SOURCE_BYTES + 1))
        except OSError as error:
            raise scenarios.ScenarioUsageError(f"eval: cannot read stdin: {error}") from error
        if len(payload) > MAX_EVAL_SOURCE_BYTES:
            raise scenarios.ScenarioUsageError(
                f"eval: source exceeds {MAX_EVAL_SOURCE_BYTES} bytes"
            )
        try:
            expression = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise scenarios.ScenarioUsageError("eval: stdin must be UTF-8") from error
    else:  # pragma: no cover - argparse enforces one source
        raise scenarios.ScenarioUsageError("eval: one source is required")
    size = len(expression.encode())
    if size > MAX_EVAL_SOURCE_BYTES:
        raise scenarios.ScenarioUsageError(f"eval: source exceeds {MAX_EVAL_SOURCE_BYTES} bytes")
    if not expression:
        raise scenarios.ScenarioUsageError("eval: source must not be empty")
    return expression, source


def cmd_text(args) -> None:
    with _client(args) as c:
        result = js.get_text(c, args.options.selector)
        _assert_session_current(args, c)
        _out(args, result)


def cmd_html(args) -> None:
    with _client(args) as c:
        result = js.get_html(c, args.options.selector)
        _assert_session_current(args, c)
        _out(args, result)


def cmd_count(args) -> None:
    with _client(args) as c:
        result = js.count(c, args.options.selector)
        _assert_session_current(args, c)
        _out(args, result)


def cmd_click(args) -> None:
    with _client(args) as c:
        result = inputs.click(c, args.options.selector)
        _assert_session_current(args, c)
        _out(args, result)


def cmd_type(args) -> None:
    text = _resolve_sensitive_value(
        args,
        literal=None,
        env_name=args.options.secret_env,
        label="type",
    )
    with _client(args) as c:
        result = inputs.type_text(
            c,
            args.options.selector,
            text,
            clear=args.options.clear,
            mode="key_events" if args.options.key_events else "insert_text",
            origin_guard=lambda remaining: _assert_session_current(
                args,
                c,
                timeout=remaining() if remaining is not None else None,
            ),
        )
        _assert_session_current(args, c)
        _out(args, result)


def cmd_key(args) -> None:
    with _client(args) as c:
        result = inputs.press_key(c, args.options.key)
        _assert_session_current(args, c)
        _out(args, result)


def register_commands(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = sub.add_parser("tabs", help="inspection of the assigned target")
    parser.add_argument("action", choices=["list"])
    parser.set_defaults(func=cmd_tabs)
    sub.add_parser("version", help="browser info").set_defaults(func=cmd_version)

    parser = sub.add_parser("goto", help="navigate to a URL")
    parser.add_argument("url")
    parser.add_argument("--wait", choices=["load", "domcontentloaded", "none"], default="load")
    parser.set_defaults(func=cmd_goto)

    parser = sub.add_parser("wait", help="wait for a CSS selector")
    parser.add_argument("selector")
    parser.set_defaults(func=cmd_wait)

    parser = sub.add_parser("eval", help="evaluate JS in the page")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("expression", nargs="?")
    source.add_argument("--file", dest="expression_file", help="read UTF-8 JS from a file")
    source.add_argument(
        "--stdin",
        dest="expression_stdin",
        action="store_true",
        help="read UTF-8 JS from stdin",
    )
    parser.add_argument("--await", dest="await_promise", action="store_true")
    parser.set_defaults(func=cmd_eval)

    parser = sub.add_parser("text", help="innerText (element or body)")
    parser.add_argument("selector", nargs="?", default=None)
    parser.set_defaults(func=cmd_text)

    parser = sub.add_parser("html", help="outerHTML (element or document)")
    parser.add_argument("selector", nargs="?", default=None)
    parser.set_defaults(func=cmd_html)

    parser = sub.add_parser("count", help="count elements matching a selector")
    parser.add_argument("selector")
    parser.set_defaults(func=cmd_count)

    parser = sub.add_parser("click", help="click an element (Input domain)")
    parser.add_argument("selector")
    parser.set_defaults(func=cmd_click)

    parser = sub.add_parser("type", help="type text into a field")
    parser.add_argument("selector")
    parser.add_argument("--secret-env", required=True, help="read the text from this variable")
    parser.add_argument("--clear", action="store_true", help="clear the field first")
    parser.add_argument(
        "--key-events",
        action="store_true",
        help="emit one trusted keyboard event sequence per printable ASCII character",
    )
    parser.set_defaults(func=cmd_type)

    parser = sub.add_parser(
        "key",
        help="press a named key (navigation, editing, Enter/Tab/Escape/Space)",
    )
    parser.add_argument("key")
    parser.set_defaults(func=cmd_key)
