"""The CLI end-to-end (in-process): args parsing -> discovery -> WS ->
primitive -> JSON on stdout + exit code. This is the contract seen by the agent."""

import json
import pathlib
from pathlib import Path

import pytest

from cdpx import __version__
from cdpx.cli import _build_redaction_context, _prepare_args, build_parser, main
from cdpx.cli_context import CommandInvocation, CommandOptions, SessionArtifactPolicy
from cdpx.client import CDPTransportError
from cdpx.policy import Authority
from cdpx.primitives import nav


@pytest.fixture(autouse=True)
def managed_cli_session(cli_manifest):
    return cli_manifest


def run(mock, capsys, *argv):
    manifest = mock.cli_manifest
    code = main(
        [
            "--session",
            str(mock.cli_manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "--timeout",
            "5",
            *argv,
        ]
    )
    out = capsys.readouterr()
    return code, out.out, out.err


def test_prepare_builds_immutable_typed_invocation(cli_manifest, mock):
    """Preparation normalizes options into an explicit context without
    enriching the argparse Namespace with hidden private attributes."""
    manifest = cli_manifest
    namespace = build_parser().parse_args(
        [
            "--session",
            str(mock.cli_manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "version",
        ]
    )
    parsed_values = vars(namespace).copy()
    options = CommandOptions.from_namespace(namespace)
    invocation = CommandInvocation(options, _build_redaction_context(options))

    prepared = _prepare_args(invocation)

    assert vars(namespace) == parsed_values
    assert prepared.execution == manifest.execution_context()
    assert prepared.manifest == manifest
    assert isinstance(prepared.artifacts, SessionArtifactPolicy)


def test_command_options_convert_cli_domain_values():
    goto = CommandOptions.from_namespace(
        build_parser().parse_args(["goto", "http://demo.test/", "--wait", "none"])
    )
    storage = CommandOptions.from_namespace(
        build_parser().parse_args(["storage", "--kind", "session"])
    )
    lifecycle = CommandOptions.from_namespace(
        build_parser().parse_args(
            [
                "session",
                "start",
                "--run-id",
                "R1",
                "--authority",
                "privileged",
                "--origins",
                "http://demo.test",
            ]
        )
    )

    assert goto.wait == "none"
    assert storage.kind == "session"
    assert lifecycle.authority is Authority.PRIVILEGED


@pytest.mark.parametrize(("field", "value"), [("wait", "later"), ("kind", "memory")])
def test_command_options_reject_invalid_domain_values(field, value):
    namespace = build_parser().parse_args(["version"])
    setattr(namespace, field, value)

    with pytest.raises(RuntimeError, match="invalid CLI"):
        CommandOptions.from_namespace(namespace)


def test_tabs_list(mock, capsys):
    """The tab inventory returns the single supervised target in JSON,
    identifiable by the agent (type page + id), with a success exit."""
    code, out, _ = run(mock, capsys, "tabs", "list")
    #: the stdout contract is kept: exit 0 and a parseable JSON object
    assert code == 0
    payload = json.loads(out)
    #: the supervised session exposes only one page, with enough to target it
    assert payload["count"] == 1
    assert payload["tabs"][0]["type"] == "page" and "id" in payload["tabs"][0]


@pytest.mark.parametrize("action", ["new", "activate", "close"])
def test_tabs_lifecycle_actions_are_absent(action):
    """Tab lifecycle actions (new/activate/close) have been removed from
    the CLI: argparse rejects them before any connection."""
    #: the removed subcommand fails at parsing, without touching CDP
    with pytest.raises(SystemExit) as exc:
        main(["tabs", action])
    #: exit 2 = usage error, not a disguised runtime error
    assert exc.value.code == 2


def test_goto(mock, capsys):
    """A successful navigation returns ok + the expected event in JSON with
    exit 0: the minimal signal the agent needs to keep going."""
    code, out, _ = run(mock, capsys, "goto", "http://site.test/")
    data = json.loads(out)
    #: the output explicitly states that load was reached, not just "ok"
    assert code == 0 and data["ok"] is True and data["waited"] == "load"


@pytest.mark.scenario(
    feature="browser-navigation",
    journey="open-page",
    scenario_id="browser-navigation.open-page-success",
    proves=["A CDP navigation error is surfaced as runtime exit 1."],
)
def test_goto_error_result_exits_1(mock, capsys, monkeypatch):
    """A CDP navigation failure (errorText) becomes exit 1 with the reason
    on stderr, instead of a deceptively green JSON on stdout."""

    def fail_navigation(*_args, **_kwargs):
        raise nav.NavigationError(
            {
                "url": "http://bad.test",
                "ok": False,
                "errorText": "ERR_NAME_NOT_RESOLVED",
            }
        )

    monkeypatch.setattr("cdpx.commands.navigation.nav.navigate", fail_navigation)
    code, _, err = run(mock, capsys, "goto", "http://bad.test")
    #: the network error propagates as a diagnosed runtime failure on stderr
    assert code == 1 and "ERR_NAME_NOT_RESOLVED" in err


def test_transport_failure_exits_1_instead_of_returning_partial_success(mock, capsys, monkeypatch):
    def fail_transport(*_args, **_kwargs):
        raise CDPTransportError("transport interrupted during collection")

    monkeypatch.setattr("cdpx.commands.navigation.nav.navigate", fail_transport)

    code, out, err = run(mock, capsys, "goto", "http://site.test/")

    assert code == 1
    assert out == ""
    assert "transport interrupted" in err


def test_connection_failure_exits_1_with_transport_diagnostic(mock, capsys, monkeypatch):
    def fail_connect(*_args, **_kwargs):
        raise CDPTransportError("CDP connection to target impossible")

    monkeypatch.setattr("cdpx.commands.shared.CDPClient", fail_connect)

    code, out, err = run(mock, capsys, "goto", "http://site.test/")

    assert code == 1
    assert out == ""
    assert "CDP connection" in err


def test_send_failure_exits_1_with_transport_diagnostic(mock, capsys, monkeypatch):
    def fail_send(*_args, **_kwargs):
        raise CDPTransportError("transport interrupted while sending Page.enable")

    monkeypatch.setattr("cdpx.client.CDPClient.send", fail_send)

    code, out, err = run(mock, capsys, "goto", "http://site.test/")

    assert code == 1
    assert out == ""
    assert "sending Page.enable" in err


def test_eval(mock, capsys):
    """eval returns the JS value computed in the page and labels it as
    untrusted content, distinct from data produced by the harness."""
    mock.on_eval("6 * 7", 42)
    code, out, _ = run(mock, capsys, "eval", "6 * 7")
    payload = json.loads(out)
    #: the value evaluated page-side comes back unchanged to the agent
    assert code == 0 and payload["value"] == 42
    #: any content coming from the page carries the untrusted marker
    assert payload["_cdpx"]["content_trust"] == "untrusted"


def test_pretty_output_is_explicit(mock, capsys):
    """JSON indentation is opt-in: the CLI stays compact by default for
    agents, and only indents on the explicit --pretty request."""
    manifest = mock.cli_manifest
    code = main(
        [
            "--session",
            str(mock.cli_manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "--pretty",
            "eval",
            "1",
        ]
    )
    out = capsys.readouterr().out
    #: the flag produces multi-line JSON, proof that it took effect
    assert code == 0
    assert out.startswith("{\n")


def test_agent_output_bounds_large_lists(mock, capsys):
    """--limit bounds large lists without silent loss: the truncation and
    the real total are announced, the agent knows what's missing."""
    events = []
    for i in range(3):
        events.append(
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": f"R{i}",
                    "type": "Fetch",
                    "request": {"url": f"http://s.test/{i}", "method": "GET"},
                },
            }
        )
    mock.script_network(events)
    code, out, _ = run(mock, capsys, "--limit", "2", "network", "http://s.test/")
    data = json.loads(out)
    #: the list is cut to the requested limit, and the output admits the
    #: cut by giving the real count of observed requests
    assert code == 0
    assert len(data["requests"]) == 2
    assert data["requests_truncated"] is True
    assert data["requests_total"] == 3


def test_console_follow_outputs_compact_ndjson(mock, capsys):
    """console --follow emits one compact NDJSON object per message, in
    arrival order, each line marked as untrusted content."""
    mock.script_console(
        [
            {
                "type": "log",
                "args": [{"type": "string", "value": "one"}],
                "timestamp": 1.0,
            },
            {
                "type": "error",
                "args": [{"type": "string", "value": "two"}],
                "timestamp": 2.0,
            },
        ]
    )
    code, out, _ = run(mock, capsys, "console", "--follow", "--max", "2")
    lines = [json.loads(line) for line in out.splitlines()]
    #: each stdout line is a self-contained object, returned in the
    #: emission order of the console messages
    assert code == 0
    assert [(item["type"], item["text"]) for item in lines] == [
        ("log", "one"),
        ("error", "two"),
    ]
    #: the messages come from the page: all labeled untrusted
    assert all(item["_cdpx"]["content_trust"] == "untrusted" for item in lines)


def test_seo_with_navigation(mock, capsys):
    """seo <url> first navigates to the page to audit then analyzes it: a
    healthy SEO page produces no stray finding."""
    payload = {
        "url": "u",
        "lang": "fr",
        "title": "T",
        "metas": {"description": "d"},
        "canonical": "c",
        "robots": None,
        "h1": ["H"],
        "hreflang": [],
        "jsonld": [],
        "images_without_alt": 0,
        "links": {"internal": 0, "external": 0, "nofollow": 0},
    }
    mock.on_eval("__cdpx_seo", json.dumps(payload))
    code, out, _ = run(mock, capsys, "seo", "http://site.test/seo.html")
    data = json.loads(out)
    #: auditing a healthy page stays silent: no false positives
    assert code == 0 and data["findings"] == []
    #: the navigation to the audited URL was actually emitted on the protocol
    assert mock.commands_for("Page.navigate") == [{"url": "http://site.test/seo.html"}]


@pytest.mark.scenario(
    feature="state-session",
    journey="read-session",
    scenario_id="state-session.redact-sensitive-session-data",
    proves=["The cookie value is redacted by default: no session secret leaks."],
)
def test_cookies_masked_output(mock, capsys, evidence_case):
    """Cookie values are redacted by default in the output: no session
    secret leaks into the agent's transcript."""
    code, out, _ = run(mock, capsys, "cookies", "get")
    data = json.loads(out)
    #: the secret value only appears in its redacted form
    assert code == 0 and data["cookies"][0]["value"] == "***"
    # secondary proof: the already-masked output feeds the cockpit without exposing the canary
    if evidence_case is not None:
        evidence_case.attach_command_output(
            "cookies get (redacted values)",
            ["cdpx", "cookies", "get"],
            out,
            "",
            code,
        )


@pytest.mark.parametrize(
    "argv",
    [
        ("cookies", "set", "--name", "flag"),
        ("cookies", "get", "--url", "http://x.test/"),
    ],
)
@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["Invalid conditional arguments fail with usage exit 2 before CDP."],
)
def test_conditional_cli_arguments_exit_2_before_discovery(mock, capsys, argv):
    """An invalid argument combination is settled as a usage error
    (exit 2) with an explicit reason, before any discovery or CDP command."""
    code, _, err = run(mock, capsys, *argv)
    #: invalid usage exits with 2 and an actionable diagnostic
    assert code == 2 and ("required" in err or "not supported" in err)
    #: the refusal precedes the protocol: nothing was emitted to Chrome
    assert mock.commands == []


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["Mutating command variants cannot bypass the configured origin guard."],
)
def test_cookie_mutations_and_vitals_click_use_origin_guard(mock, capsys, monkeypatch):
    """Disguised mutating variants (cookies set --url, vitals --click) go
    through the origin guard: off the list, they are refused."""
    monkeypatch.setenv("COOKIE_FLAG", "1")
    for argv in (
        (
            "cookies",
            "set",
            "--name",
            "flag",
            "--value-env",
            "COOKIE_FLAG",
            "--url",
            "https://prod.example/",
        ),
        ("vitals", "https://prod.example/", "--click", "#go"),
    ):
        code, _, err = run(mock, capsys, *argv)
        #: each mutating variant is refused with the origin reason, none
        #: bypasses the configured guard
        assert code == 1 and "origin rejected" in err


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="intercept-network",
    scenario_id="orchestration-control.intercept-network-request",
    proves=["The origin guard judges the destination of the composed goto, not the initial tab."],
)
def test_intercept_checks_destination_origin_not_initial_tab(mock, capsys, monkeypatch):
    """intercept's origin guard judges the destination URL of the composed
    goto, not the initial tab: an allowed tab does not whitewash a
    navigation to a forbidden origin."""
    monkeypatch.setenv("CDPX_ORIGINS", "http://*.test")
    tid = next(iter(mock.targets))
    mock.targets[tid]["url"] = "http://allowed.test/"
    code, _, err = run(
        mock,
        capsys,
        "intercept",
        "--rule",
        "* => block",
        "--",
        "goto",
        "https://prod.example/",
    )
    #: the forbidden destination is refused despite the allowed tab, and
    #: the refusal happens before any CDP command
    assert code == 1 and "origin rejected" in err
    assert mock.commands == []


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["A mutating click on a disallowed origin is refused before the input protocol."],
)
def test_origin_guard_blocks_cli_mutation(mock, capsys, monkeypatch):
    """A mutation (click) targeting a non-allowed origin is refused before
    reaching the input protocol: the page stays untouched."""
    target = next(iter(mock.targets))
    mock.targets[target]["url"] = "https://prod.example/"
    code, _, err = run(mock, capsys, "click", "#submit")
    #: the refusal is a runtime error diagnosed on stderr
    assert code == 1
    assert "origin rejected" in err
    #: no mouse event was dispatched to the page
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_origin_guard_blocks_dom_diff(mock, capsys, monkeypatch):
    """dom-diff executes a real mutating action: the wrapper undergoes the
    same origin guard as the mutation it carries."""
    # dom-diff executes real mutations (click/type/key/eval): same guard as click.
    target = next(iter(mock.targets))
    mock.targets[target]["url"] = "https://prod.example/"
    code, _, err = run(mock, capsys, "dom-diff", "--", "click", "#x")
    #: wrapping the mutation in dom-diff offers no bypass
    assert code == 1
    assert "origin rejected" in err
    #: the wrapped click never reached the page
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_origin_guard_allows_dom_diff_on_allowed_origin(mock, capsys, monkeypatch):
    """On an explicitly allowed origin, dom-diff executes the wrapped
    action and reports the observed DOM mutation."""
    monkeypatch.setenv("CDPX_ORIGINS", "http://*.test")
    tid = next(iter(mock.targets))
    mock.targets[tid]["url"] = "http://demo.test/page"
    mock.on_eval("__cdpx_dom_snapshot", json.dumps(["<body>"]), json.dumps(["<body>", "  <p>"]))
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    code, out, _ = run(mock, capsys, "dom-diff", "--", "click", "#x")
    #: the guard lets the listed origin through and the diff sees the change
    assert code == 0 and json.loads(out)["changed"] is True


def test_screenshot(mock, capsys, tmp_path):
    """screenshot captures png via the protocol and files the output in
    the session's supervised artifacts, not at the raw requested path."""
    dest = tmp_path / "s.png"
    code, out, _ = run(mock, capsys, "screenshot", "-o", str(dest))
    data = json.loads(out)
    #: the file lives in the supervised artifacts; the raw -o path is
    #: never written directly
    assert code == 0 and Path(data["path"]).exists() and not dest.exists()
    #: the protocol did receive a png capture request
    assert mock.commands_for("Page.captureScreenshot")[0]["format"] == "png"


def test_screenshot_format_jpeg(mock, capsys, tmp_path):
    """--format jpeg propagates all the way to the CDP capture command and
    to the format field of the JSON output."""
    dest = tmp_path / "s.jpg"
    code, out, _ = run(mock, capsys, "screenshot", "-o", str(dest), "--format", "jpeg")
    data = json.loads(out)
    #: the requested format is reflected in the output and the written file
    assert code == 0 and Path(data["path"]).exists() and data["format"] == "jpeg"
    #: the CDP capture was emitted as jpeg, not with the default png
    assert mock.commands_for("Page.captureScreenshot")[0]["format"] == "jpeg"


RECT = json.dumps({"x": 0, "y": 0, "width": 10, "height": 10})

# Dispatch net: each subcommand traverses argparse -> _client -> primitive
# -> JSON stdout, and emits at least its signature CDP command (protocol contract).
# Format: (id, argv, on_eval rules, expected CDP method, predicate on the output)
DISPATCH_CASES = [
    ("wait", ["wait", "#late"], {"querySelector": True}, "Runtime.evaluate", lambda d: d["found"]),
    (
        "text",
        ["text"],
        {"innerText": "Hello"},
        "Runtime.evaluate",
        lambda d: d["text"] == "Hello",
    ),
    (
        "html",
        ["html", "#x"],
        {"outerHTML": "<b>x</b>"},
        "Runtime.evaluate",
        lambda d: d["html"] == "<b>x</b>",
    ),
    (
        "count",
        ["count", ".item"],
        {"querySelectorAll": 3},
        "Runtime.evaluate",
        lambda d: d["count"] == 3,
    ),
    (
        "click",
        ["click", "#go"],
        {"getBoundingClientRect": RECT},
        "Input.dispatchMouseEvent",
        lambda d: d["clicked"] == "#go",
    ),
    (
        "type",
        ["type", "#name", "--secret-env", "CLI_TEXT"],
        {"focus": True},
        "Input.insertText",
        lambda d: d["typed"] is True and d["value_masked"] is True,
    ),
    ("key", ["key", "Enter"], {}, "Input.dispatchKeyEvent", lambda d: d["pressed"] == "Enter"),
    (
        "network",
        ["network", "http://s.test/", "--settle", "0.1"],
        {},
        "Page.navigate",
        lambda d: "summary" in d,
    ),
    ("storage", ["storage"], {"localStorage": "{}"}, "Runtime.evaluate", lambda d: d["count"] == 0),
    ("metrics", ["metrics"], {}, "Performance.getMetrics", lambda d: d["Nodes"] == 42),
    ("a11y", ["a11y"], {}, "Accessibility.getFullAXTree", lambda d: d["count"] == 2),
    (
        "coverage",
        ["coverage", "http://s.test/"],
        {},
        "Profiler.startPreciseCoverage",
        lambda d: d["css"]["rules"] == 2,
    ),
    (
        "frame",
        ["frame", "#m"],
        {"contentDocument": "iframe text"},
        "Runtime.evaluate",
        lambda d: d["text"] == "iframe text",
    ),
    (
        "vitals",
        ["vitals", "http://s.test/", "--settle", "0.1"],
        {"__cdpxVitals": json.dumps({"lcp": 1, "cls": 0, "inp": 0})},
        "Page.addScriptToEvaluateOnNewDocument",
        lambda d: d["lcp"] == 1,
    ),
    (
        "emulate",
        ["emulate", "slow-3g"],
        {},
        "Network.emulateNetworkConditions",
        lambda d: d["applied"] is True,
    ),
    (
        "dom-diff",
        ["dom-diff", "--", "eval", "1 + 1"],
        {"__cdpx_dom_snapshot": json.dumps(["<body>"])},
        "Runtime.evaluate",
        lambda d: d["changed"] is False,
    ),
]


@pytest.mark.parametrize(
    "case_id,argv,rules,method,check", DISPATCH_CASES, ids=[c[0] for c in DISPATCH_CASES]
)
@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["Each catalog subcommand reaches its primitive and emits its signature CDP command."],
)
def test_cli_dispatch_emits_protocol_and_json(
    mock, capsys, monkeypatch, case_id, argv, rules, method, check
):
    """Dispatch net: each catalog subcommand traverses argparse -> client
    -> primitive -> JSON stdout, and emits at least its signature CDP
    command (the expected protocol IS the spec)."""
    monkeypatch.setenv("CLI_TEXT", "Léo")
    for substring, value in rules.items():
        mock.on_eval(substring, value)
    code, out, err = run(mock, capsys, *argv)
    #: the subcommand succeeds; stderr is joined to the diagnostic otherwise
    assert code == 0, f"{case_id}: exit {code}, stderr={err}"
    data = json.loads(out)
    #: the JSON output carries the signature data expected for this case
    assert check(data), f"{case_id}: unexpected output {data}"
    if method:
        #: the case's signature CDP command was actually emitted
        assert mock.commands_for(method), f"{case_id}: {method} never emitted"


def test_pdf_cli_writes_valid_signature(mock, capsys, tmp_path, evidence_case):
    """pdf produces a real document (%PDF signature) via the CDP print
    command, filed in the session's supervised artifacts."""
    dest = tmp_path / "page.pdf"
    code, out, _ = run(mock, capsys, "pdf", "-o", str(dest))
    data = json.loads(out)
    #: the output announces non-empty content, not a phantom file
    assert code == 0 and data["bytes"] > 0
    #: the written file is a real PDF and lives in the supervised
    #: artifacts, never at the raw -o path
    assert Path(data["path"]).read_bytes().startswith(b"%PDF") and not dest.exists()
    #: the print went through the protocol, not through a shortcut
    assert mock.commands_for("Page.printToPDF")
    # secondary proof: the binary PDF (opaque, not inlined) + a readable summary in the modal
    if evidence_case is not None:
        evidence_case.attach_file(data["path"], "Printed PDF (%PDF signature)")
        evidence_case.attach_json(
            "Observed PDF signature",
            {
                "signature": "%PDF",
                "bytes": data["bytes"],
                "artifact_basename": Path(data["path"]).name,
            },
        )


def test_dom_diff_accepts_action_with_or_without_separator(mock, capsys):
    """dom-diff accepts the composed action with or without `--`, and
    redacts the action's arguments in the output (the expression may be
    secret)."""
    mock.on_eval("__cdpx_dom_snapshot", json.dumps(["<body>"]))
    mock.on_eval("2 + 2", 4)
    code, out, _ = run(mock, capsys, "dom-diff", "eval", "2 + 2")
    #: without a separator, the action passes and its arguments are redacted
    assert code == 0 and json.loads(out)["action"] == ["eval", "***"]
    code, out, _ = run(mock, capsys, "dom-diff", "--", "eval", "2 + 2")
    #: with `--`, same contract: the separator changes nothing about the result
    assert code == 0 and json.loads(out)["action"] == ["eval", "***"]


def test_profiler_cli_panels_flag(mock, capsys):
    """--panels db retrieves and summarizes the Symfony profiler's Doctrine
    panel without ever exposing the debug token or the global-mode fields."""
    db_html = (pathlib.Path(__file__).parent / "fixtures" / "profiler" / "db.html").read_text(
        encoding="utf-8"
    )
    mock.script_network(
        [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": "http://s.test/",
                        "status": 200,
                        "headers": {"X-Debug-Token-Link": "http://s.test/_profiler/tok"},
                    },
                },
            }
        ]
    )
    mock.on_eval(
        "__cdpx_profiler_panels",
        json.dumps([{"panel": "db", "status": 200, "html": db_html}]),
    )
    mock.on_eval("window.location.href", "http://s.test/")
    code, out, _ = run(
        mock, capsys, "profiler", "http://s.test/", "--settle", "0.05", "--panels", "db"
    )
    data = json.loads(out)
    assert code == 0
    #: the token is reported present but its secret value never leaks
    assert data["token_present"] is True and "token" not in data
    #: the requested panel is parsed all the way to the SQL query count
    assert data["panels"]["db"]["queries"] == 6
    #: panels mode stays targeted: no global analysis embedded
    assert "signals" not in data and "profiler_bytes" not in data


def test_profiler_cli_unknown_panel_is_usage_error(mock, capsys):
    """A nonexistent profiler panel is rejected as a usage error with a
    message naming the problem, before any navigation."""
    #: the unknown panel fails at parsing with exit 2
    with pytest.raises(SystemExit) as exc:
        run(mock, capsys, "profiler", "http://s.test/", "--panels", "doctrine")
    assert exc.value.code == 2
    #: the diagnostic names the cause to fix the invocation
    assert "unknown panel(s)" in capsys.readouterr().err


def test_intercept_multiple_rules_and_invalid_action(mock, capsys):
    """intercept applies several simultaneous rules (the matching rule
    responds) and refuses any composed action other than goto before
    arming the slightest interception."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {
                    "requestId": "P1",
                    "request": {"url": "http://s.test/api/x"},
                },
            }
        ]
    )
    code, out, _ = run(
        mock,
        capsys,
        "intercept",
        "--rule",
        "*api* => 503",
        "--rule",
        "*img* => block",
        "--settle",
        "0.1",
        "--",
        "goto",
        "http://s.test/",
    )
    data = json.loads(out)
    #: both rules are armed and the intercepted api request actually
    #: received the promised 503 via the Fetch protocol
    assert code == 0 and len(data["rules"]) == 2
    assert mock.commands_for("Fetch.fulfillRequest")[0]["responseCode"] == 503
    # non-goto action: usage error BEFORE any Fetch command
    mock.commands.clear()
    code, _, err = run(mock, capsys, "intercept", "--rule", "*x* => block", "--", "click", "#x")
    #: the unsupported action is refused without emitting a command
    assert code == 1 and "intercept supports" in err and mock.commands == []


def test_emulate_requires_preset_or_reset(mock, capsys):
    """emulate without a preset or --reset fails with a named reason: no
    silent implicit emulation."""
    code, _, err = run(mock, capsys, "emulate")
    #: the missing preset is an explicit runtime failure, not a no-op
    assert code == 1 and "unknown preset" in err


def test_record_cli_executes_and_journals(mock, capsys, tmp_path):
    """record actually executes the composed action and journals each
    event in replayable NDJSON, without leaking the `--` separator."""
    journal = tmp_path / "j.ndjson"
    code, out, _ = run(mock, capsys, "record", "-o", str(journal), "--", "goto", "http://a.test/")
    data = json.loads(out)
    #: the action ran and exactly one event was journaled
    assert code == 0 and data["ok"] is True and data["recorded"] == 1
    #: the recorded navigation was actually emitted on the protocol
    assert mock.commands_for("Page.navigate") == [{"url": "http://a.test/"}]
    event = json.loads(Path(data["path"]).read_text().splitlines()[0])
    #: the journal captures the cleaned action, replayable as-is
    assert event["action"] == ["goto", "http://a.test/"]  # the `--` does not leak into the journal


def test_replay_cli_divergence_exits_1_with_json(mock, capsys, tmp_path, evidence_case):
    """A replay that diverges (selector gone) exits with 1 while keeping a
    structured JSON that locates the faulty event."""
    journal = Path(mock.cli_manifest.artifacts_dir) / "journals" / "j.ndjson"
    journal.parent.mkdir(parents=True, mode=0o700)
    journal.write_text('{"action":["click","#gone"],"ok":true}\n', encoding="utf-8")
    journal.chmod(0o600)
    mock.on_eval("getBoundingClientRect", None)
    code, out, _ = run(mock, capsys, "replay", str(journal))
    data = json.loads(out)
    #: divergence is an execution error, not a usage error
    assert code == 1  # divergence = execution error, structured JSON kept
    #: the JSON survives the failure and points at the diverging event
    assert data["ok"] is False and data["divergence"].startswith("event 0:")
    # secondary proof: the structured divergence JSON (event 0:) illustrates the replay contract
    if evidence_case is not None:
        evidence_case.attach_command_output(
            "diverging replay (exit 1, event 0:)",
            ["cdpx", "replay", journal.name],
            out,
            "",
            code,
        )


def test_replay_cli_green_journal_exits_0(mock, capsys, tmp_path):
    """A journal replayed without divergence exits with 0 and the full
    count of played events: the proof of the replay is quantified."""
    journal = Path(mock.cli_manifest.artifacts_dir) / "journals" / "j.ndjson"
    journal.parent.mkdir(parents=True, mode=0o700)
    journal.write_text('{"action":["goto","http://a.test/"],"ok":true}\n', encoding="utf-8")
    journal.chmod(0o600)
    code, out, _ = run(mock, capsys, "replay", str(journal))
    data = json.loads(out)
    #: the green replay identifies the source journal in its output
    assert code == 0 and data["path"] == str(journal)
    #: all events were played, none silently skipped
    assert data["events"] == 1 and data["played"] == 1 and data["ok"] is True


def test_emulate_composed_action_runs_in_same_connection(mock, capsys):
    """emulate <preset> -- <action> sets the overrides then plays the
    action in the same WS connection: the action sees emulation active
    (the overrides die at disconnection)."""
    code, out, _ = run(mock, capsys, "emulate", "mobile", "--", "goto", "http://a.test/")
    data = json.loads(out)
    #: both the emulation and the composed action succeed
    assert code == 0 and data["applied"] is True
    assert data["action"]["result"]["ok"] is True
    # the preset is set BEFORE the action, in the same connection
    methods = [m for (_t, m, _p) in mock.commands]
    #: the protocol order proves that the preset precedes navigation, so
    #: the loaded page does undergo emulation
    assert methods.index("Emulation.setDeviceMetricsOverride") < methods.index("Page.navigate")


def test_origin_guard_composed_commands_follow_action_verb(mock, capsys, monkeypatch, tmp_path):
    """The origin guard for composed commands (record/replay) judges the
    verb of the wrapped action: mutation refused, read allowed, and
    replay is guarded sequentially rather than on the initial tab."""
    journal = Path(mock.cli_manifest.artifacts_dir) / "journals" / "j.ndjson"
    target = next(iter(mock.targets))
    mock.targets[target]["url"] = "https://prod.example/"
    # record with a mutating verb: refused (no CDP command emitted)
    code, _, err = run(mock, capsys, "record", "-o", str(journal), "--", "click", "#x")
    #: the wrapped mutating verb is refused before reaching the page
    assert code == 1 and "origin rejected" in err
    assert mock.commands_for("Input.dispatchMouseEvent") == []
    # replay is guarded sequentially: a read navigation to an allowed
    # origin is no longer refused because of the initial about:blank tab.
    journal.parent.mkdir(parents=True, mode=0o700)
    journal.write_text('{"action":["goto","http://a.test/"],"ok":true}\n', encoding="utf-8")
    journal.chmod(0o600)
    mock.on_eval("window.location.href", "http://a.test/")
    code, out, err = run(mock, capsys, "replay", str(journal))
    #: the replayed read navigation passes despite the initial
    #: about:blank tab: the guard follows the sequence, not the starting state
    assert code == 0 and json.loads(out)["ok"] is True and not err
    # record with a read verb: allowed even off the list
    code, out, _ = run(mock, capsys, "record", "-o", str(journal), "--", "goto", "http://a.test/")
    #: a read verb does not require a listed origin for record
    assert code == 0 and json.loads(out)["ok"] is True


def test_error_path_exit_code_and_stderr(mock, capsys):
    """A JS exception thrown in the page becomes exit 1 with the error
    message on stderr, stdout staying reserved for JSON."""
    mock.on_eval("kaboom", {"raw": {"exceptionDetails": {"text": "TypeError: kaboom"}}})
    code, _, err = run(mock, capsys, "eval", "kaboom()")
    #: the page's exception propagates as a diagnosed runtime failure
    assert code == 1 and "kaboom" in err


def test_missing_session_fails_before_discovery(capsys, monkeypatch):
    """Without a supervised session (CDPX_* variables absent), the CLI
    fails as a usage error naming the missing variable, before any discovery."""
    for name in ("CDPX_SESSION", "CDPX_RUN_ID", "CDPX_TARGET"):
        monkeypatch.delenv(name, raising=False)
    code = main(["tabs", "list"])
    err = capsys.readouterr().err
    #: the absent session is an exit 2 that says what to export
    assert code == 2 and "CDPX_SESSION" in err


def test_invalid_action_argv_without_session_stays_usage_error(capsys, monkeypatch):
    """An invalid action argv does not short-circuit the session
    diagnostic: redaction is built without parsing the action, and the
    missing identity stays a clean usage error, never a traceback."""
    for name in ("CDPX_SESSION", "CDPX_RUN_ID", "CDPX_TARGET"):
        monkeypatch.delenv(name, raising=False)
    code = main(["dom-diff", "--", "bogus", "x"])
    captured = capsys.readouterr()
    #: the missing identity takes priority over the unreadable action:
    #: documented exit 2
    assert code == 2 and "CDPX_SESSION" in captured.err
    #: the diagnostic stays a cdpx message, not a raw ValueError
    assert "Traceback" not in captured.err and captured.out == ""


def test_invalid_action_argv_with_session_is_diagnosed(mock, capsys):
    """With a valid session, an unknown action argv fails preflight as a
    diagnosed error on stderr, stdout staying empty."""
    code, out, err = run(mock, capsys, "dom-diff", "--", "bogus", "x")
    #: preflight rejects the unknown action with its usage, exit 1
    assert code == 1 and "cdpx:" in err and "action" in err
    #: no raw traceback nor misleading JSON on stdout
    assert "Traceback" not in err and out == ""


@pytest.mark.parametrize("option", ["--host", "--port"])
def test_direct_connection_options_are_removed(option):
    """Direct connection options (--host/--port) have disappeared from the
    CLI: only the supervised session can designate the target Chrome."""
    #: the removed option is rejected at parsing as an unknown argument
    with pytest.raises(SystemExit) as exc:
        main([option, "1", "tabs", "list"])
    #: exit 2 confirms that direct connection no longer exists
    assert exc.value.code == 2


def test_usage_error_exit_2():
    """A missing positional argument is settled by argparse as exit 2,
    distinct from the CLI contract's runtime errors (exit 1)."""
    #: goto without a url fails at parsing, before any connection
    with pytest.raises(SystemExit) as exc:
        main(["goto"])  # missing url
    assert exc.value.code == 2


def test_cdpx_version(capsys):
    """--version prints exactly `cdpx <version>` and exits with 0: the
    number comes from the package's single __version__ source."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    #: requesting the version is a success, not a usage error
    assert exc.value.code == 0
    #: the output reflects the package's single version, nothing else
    assert capsys.readouterr().out == f"cdpx {__version__}\n"
