"""Every primitive is validated against the mock: we check both the OUTPUT
(stable JSON contract) and the emitted PROTOCOL (recorded methods/params)."""

import json
import pathlib
import stat

import pytest

from cdpx import discovery
from cdpx.action_model import ClickAction, EvalAction, GotoAction
from cdpx.client import CDPClient, CDPTimeout
from cdpx.orchestration import OrchestrationContext
from cdpx.primitives import (
    actions,
    audit,
    capture,
    dev,
    diagnostics,
    emulation,
    frames,
    inputs,
    interception,
    js,
    nav,
    net,
    recording,
    state,
)
from cdpx.security import RedactionContext

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def orchestration(
    origins: str = "http://*.test", redaction: RedactionContext | None = None
) -> OrchestrationContext:
    return OrchestrationContext.from_origins(origins, redaction=redaction)


@pytest.fixture()
def client(mock):
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    target = discovery.pick_page("127.0.0.1", mock.http_port, target_id)
    with CDPClient(target["webSocketDebuggerUrl"], timeout=5) as c:
        yield c


# -- nav --------------------------------------------------------------------------


def test_navigate_waits_load(mock, client):
    """Navigation only returns control after the load event, and emits
    exactly one Page.navigate, to the requested URL unchanged."""
    res = nav.navigate(client, "http://site.test/page", wait="load")
    #: success exposes the frame that was actually navigated, proof that load was awaited
    assert res["ok"] is True and res["frameId"] == "FRAME1"
    #: on the protocol side, a single navigation goes out with the URL unaltered
    assert mock.commands_for("Page.navigate") == [{"url": "http://site.test/page"}]


def test_navigate_raises_typed_error_with_failed_result(client, monkeypatch):
    real_send = client.send

    def send(method, params=None, **kwargs):
        if method == "Page.navigate":
            return {"frameId": "FRAME1", "errorText": "ERR_NAME_NOT_RESOLVED"}
        return real_send(method, params, **kwargs)

    monkeypatch.setattr(client, "send", send)

    with pytest.raises(nav.NavigationError) as excinfo:
        nav.navigate(client, "http://bad.test/")

    assert excinfo.value.result == {
        "url": "http://bad.test/",
        "frameId": "FRAME1",
        "loaderId": None,
        "errorText": "ERR_NAME_NOT_RESOLVED",
        "waited": "load",
        "ok": False,
    }


def test_navigate_rejects_unknown_wait_before_protocol(mock, client):
    with pytest.raises(ValueError, match="unknown navigation wait"):
        nav.navigate(client, "http://site.test/page", wait="networkidle")  # type: ignore[arg-type]

    assert mock.commands == []


def test_wait_for_polls_until_found(mock, client):
    """wait_for probes the DOM at a regular interval and stops as soon as the
    selector appears, without any superfluous polling afterwards."""
    mock.on_eval("querySelector", False, False, True)
    res = nav.wait_for(client, "#late-content", timeout=2, poll=0.01)
    #: three probes for three scripted responses: the loop stops as soon as
    #: the element first appears
    assert res["found"] is True
    assert len(mock.commands_for("Runtime.evaluate")) == 3


def test_wait_for_times_out(mock, client):
    """A selector that never appears raises CDPTimeout at the deadline instead
    of blocking the session indefinitely."""
    mock.on_eval("querySelector", False)
    #: the time budget turns absence into an explicit error, never an infinite wait
    with pytest.raises(CDPTimeout):
        nav.wait_for(client, "#never", timeout=0.15, poll=0.02)


def test_wait_for_visible_polls_until_element_has_a_non_zero_box(mock, client):
    """wait_for_visible demands more than DOM presence: the injected probe
    checks connection, non-hiding styles and a non-zero box before declaring
    the element visible."""
    mock.on_eval("__cdpx_visible", False, False, True)

    res = nav.wait_for_visible(client, "#late-content", timeout=2, poll=0.01)

    #: visibility is confirmed for the requested selector, not another one
    assert res["visible"] is True
    assert res["selector"] == "#late-content"
    calls = mock.commands_for("Runtime.evaluate")
    #: the loop kept probing until the state flipped, then stopped
    assert len(calls) == 3
    expression = calls[0]["expression"]
    #: the probe covers every CSS hiding mode and the real geometry,
    #: not just the presence of a node in the DOM
    assert ".isConnected" in expression
    assert 'style.display === "none"' in expression
    assert 'style.visibility === "hidden"' in expression
    assert 'style.visibility === "collapse"' in expression
    assert "rect.width > 0 && rect.height > 0" in expression


def test_wait_for_visible_times_out_while_element_stays_hidden(mock, client):
    """An element that stays hidden indefinitely raises CDPTimeout with a
    'not visible' diagnostic, distinct from pure absence from the DOM."""
    mock.on_eval("__cdpx_visible", False)

    #: the message distinguishes persistent invisibility from a selector that cannot be found
    with pytest.raises(CDPTimeout, match="not visible"):
        nav.wait_for_visible(client, "#hidden", timeout=0.15, poll=0.02)


# -- js ---------------------------------------------------------------------------


def test_evaluate_value_and_exception(mock, client):
    """evaluate returns the raw JS value and converts the protocol's
    exceptionDetails into a Python JSException carrying the original message."""
    mock.on_eval("1 + 1", 2)
    #: the computed value passes through without wrapping or conversion
    assert js.evaluate(client, "1 + 1") == 2
    mock.on_eval("boom", {"raw": {"exceptionDetails": {"text": "ReferenceError: boom"}}})
    #: a JS error becomes a typed exception on the Python side, message preserved
    with pytest.raises(js.JSException, match="boom"):
        js.evaluate(client, "boom()")


def test_get_text_and_html_and_count(mock, client):
    """Each DOM reader (text, HTML, count) wraps the evaluated value under
    its JSON contract key, without transforming the content."""
    mock.on_eval("innerText", "Bonjour")
    #: each read primitive returns the page's value under its contractual key
    assert js.get_text(client, "#intro")["text"] == "Bonjour"
    mock.on_eval("outerHTML", "<p>x</p>")
    assert js.get_html(client, "p")["html"] == "<p>x</p>"
    mock.on_eval("querySelectorAll", 3)
    assert js.count(client, "h1")["count"] == 3


# -- inputs -----------------------------------------------------------------------


ACTIONABLE = {
    "attached": True,
    "visible": True,
    "enabled": True,
    "stable": True,
    "receives_events": True,
    "editable": True,
    "rect": {"x": 10, "y": 20, "width": 100, "height": 30},
}


@pytest.mark.scenario(
    feature="dom-interaction",
    journey="submit-form",
    scenario_id="dom-interaction.submit-form-like-user",
    proves=["The click emits the moved/pressed/released mouse sequence at the element's center."],
)
def test_click_dispatches_mouse_events_at_center(mock, client, evidence_case):
    """A click first probes actionability then emits the trusted
    moved/pressed/released mouse sequence, aimed at the element's geometric
    center."""
    mock.on_eval("__cdpx_actionability", json.dumps(ACTIONABLE))

    res = inputs.click(client, "#submit-btn")

    #: the reported point is the exact center of the probed rect, not a corner
    assert (res["x"], res["y"]) == (60.0, 35.0)
    #: the complete Input sequence mimics a real user, every event at the
    #: same point and with the same button
    assert mock.commands_for("Input.dispatchMouseEvent") == [
        {
            "type": "mouseMoved",
            "x": 60.0,
            "y": 35.0,
            "button": "left",
            "clickCount": 1,
        },
        {
            "type": "mousePressed",
            "x": 60.0,
            "y": 35.0,
            "button": "left",
            "clickCount": 1,
        },
        {
            "type": "mouseReleased",
            "x": 60.0,
            "y": 35.0,
            "button": "left",
            "clickCount": 1,
        },
    ]
    (probe,) = mock.commands_for("Runtime.evaluate")
    #: the probe is an awaited promise whose value comes back serialized
    assert probe["awaitPromise"] is True
    assert probe["returnByValue"] is True
    expression = probe["expression"]
    #: the probe checks stability (double rAF), disabled state, inertness and
    #: hit-testing of the click point before allowing any event at all
    assert expression.count("requestAnimationFrame") == 2
    assert '.matches(":disabled")' in expression
    assert "aria-disabled" in expression
    assert '.closest("[inert]")' in expression
    assert "pointerEvents" in expression
    assert "document.elementFromPoint" in expression
    assert "element.contains(hit)" in expression

    # Secondary evidence: the log of emitted Input events attests the trusted
    # mouse sequence aimed at the element's geometric center.
    if evidence_case is not None:
        evidence_case.attach_json(
            "Input.dispatchMouseEvent sequence for the click (center 60,35)",
            {
                "point": {"x": res["x"], "y": res["y"]},
                "mouse_events": mock.commands_for("Input.dispatchMouseEvent"),
            },
        )


def test_click_element_not_found(mock, client):
    """A selector detached from the DOM fails the click with ElementNotFound
    without any mouse event reaching the page."""
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, "attached": False}))

    #: the absence is reported via a dedicated exception with an actionable message
    with pytest.raises(inputs.ElementNotFound, match="selector not found"):
        inputs.click(client, "#ghost")
    #: fail-closed: despite the failure, the page received no mouse event
    assert mock.commands_for("Input.dispatchMouseEvent") == []


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({"visible": False}, "not visible"),
        ({"enabled": False}, "disabled"),
        ({"stable": False}, "unstable"),
        ({"receives_events": False}, "covered"),
    ],
)
def test_click_refuses_non_actionable_element_without_input(mock, client, state, message):
    """Every actionability defect (invisible, disabled, unstable,
    covered) blocks the click with its own diagnostic, before any mouse
    event is emitted."""
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, **state}))

    #: the refusal precisely names the defect encountered, whatever the case
    with pytest.raises(inputs.ElementNotInteractable, match=message):
        inputs.click(client, "#blocked")

    #: the guard acts before the protocol: the page sees nothing go through
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_type_text_clear_selects_then_deletes_through_input_domain(mock, client):
    """--clear empties the field via selection then Backspace through the
    Input domain — never by assigning value — and the output masks the
    typed text."""
    mock.on_eval("__cdpx_actionability", json.dumps(ACTIONABLE))
    mock.on_eval("__cdpx_prepare_text", True)

    res = inputs.type_text(client, "#name", "Léo", clear=True)

    #: the output confirms typing and clearing without ever reproducing the entered text
    assert res == {
        "typed": True,
        "value_masked": True,
        "selector": "#name",
        "cleared": True,
    }
    assert "Léo" not in json.dumps(res, ensure_ascii=False)
    evaluations = mock.commands_for("Runtime.evaluate")
    #: only two evaluations: actionability probe then field preparation
    assert len(evaluations) == 2
    prepare = evaluations[1]["expression"]
    #: preparation selects the content via the DOM API, without directly
    #: assigning value, which would bypass reactive frameworks
    assert "el.select()" in prepare
    assert "range.selectNodeContents(el)" in prepare
    assert "selection.removeAllRanges()" in prepare
    assert "el.value =" not in prepare
    #: clearing is a real Backspace keystroke through the Input domain
    assert mock.commands_for("Input.dispatchKeyEvent") == [
        {
            "type": "rawKeyDown",
            "key": "Backspace",
            "code": "Backspace",
            "windowsVirtualKeyCode": 8,
        },
        {
            "type": "keyUp",
            "key": "Backspace",
            "code": "Backspace",
            "windowsVirtualKeyCode": 8,
        },
    ]
    #: the text enters via insertText, like a trusted keystroke
    assert mock.commands_for("Input.insertText") == [{"text": "Léo"}]
    methods = [method for _, method, _ in mock.commands]
    #: the protocol order is deterministic: probes, clearing, then insertion
    assert methods == [
        "Runtime.evaluate",
        "Runtime.evaluate",
        "Input.dispatchKeyEvent",
        "Input.dispatchKeyEvent",
        "Input.insertText",
    ]


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({"visible": False}, "not visible"),
        ({"enabled": False}, "disabled"),
        ({"editable": False}, "not editable"),
    ],
)
def test_type_text_refuses_invalid_target_without_input(mock, client, state, message):
    """An invisible, disabled or non-editable field blocks typing with its
    dedicated diagnostic, without any key or insertion reaching the page."""
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, **state}))

    #: the diagnostic names the precise defect that prevents input
    with pytest.raises(inputs.ElementNotInteractable, match=message):
        inputs.type_text(client, "#blocked", "secret", clear=True)

    #: nothing was typed: no keyboard event and no text insertion
    assert mock.commands_for("Input.dispatchKeyEvent") == []
    assert mock.commands_for("Input.insertText") == []


def test_type_text_refuses_missing_target_without_input(mock, client):
    """A selector absent from the DOM fails typing with ElementNotFound —
    distinct from non-interactivity — and the keyboard stays silent."""
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, "attached": False}))

    #: absence is distinguished from non-interactivity by the exception type
    with pytest.raises(inputs.ElementNotFound, match="selector not found"):
        inputs.type_text(client, "#missing", "secret", clear=True)

    #: no input, not even partial, escapes to the page
    assert mock.commands_for("Input.dispatchKeyEvent") == []
    assert mock.commands_for("Input.insertText") == []


def test_press_key_enter_sequence(mock, client):
    """Enter, a printing key, emits the full sequence with a char event;
    a key outside the registry is rejected before any emission."""
    inputs.press_key(client, "Enter")
    keys = mock.commands_for("Input.dispatchKeyEvent")
    #: Enter produces a character: the char event is interleaved in the sequence
    assert [k["type"] for k in keys] == ["rawKeyDown", "char", "keyUp"]
    #: a key absent from the supported registry is rejected outright
    with pytest.raises(ValueError):
        inputs.press_key(client, "F13")


def test_press_key_backspace_sequence(mock, client):
    """Backspace, a glyph-less key, only emits rawKeyDown/keyUp: no stray
    char event should pollute the field."""
    result = inputs.press_key(client, "Backspace")

    #: an editing key without a character does not generate a char event
    assert result == {"pressed": "Backspace"}
    assert [event["type"] for event in mock.commands_for("Input.dispatchKeyEvent")] == [
        "rawKeyDown",
        "keyUp",
    ]


@pytest.mark.parametrize(
    ("key", "types"),
    [
        ("Space", ["rawKeyDown", "char", "keyUp"]),
        ("Delete", ["rawKeyDown", "keyUp"]),
        ("Home", ["rawKeyDown", "keyUp"]),
        ("ArrowLeft", ["rawKeyDown", "keyUp"]),
        ("ArrowRight", ["rawKeyDown", "keyUp"]),
        ("PageDown", ["rawKeyDown", "keyUp"]),
    ],
)
def test_press_key_supports_common_navigation_and_editing_keys(mock, client, key, types):
    """The registry covers common navigation and editing keys, each with its
    exact sequence: the char event only appears for keys that print a
    character."""
    #: the emitted sequence matches the nature of the key (with or without a glyph)
    assert inputs.press_key(client, key) == {"pressed": key}
    assert [event["type"] for event in mock.commands_for("Input.dispatchKeyEvent")] == types


# -- capture ----------------------------------------------------------------------


def test_screenshot_writes_valid_png(mock, client, tmp_path):
    """The capture writes a real PNG protected at 0600 and propagates
    full_page through to the protocol's captureBeyondViewport parameter."""
    out = tmp_path / "shot.png"
    res = capture.screenshot(client, str(out), full_page=True)
    #: the file is a real PNG, private (0600), and its size is reported
    assert out.read_bytes().startswith(b"\x89PNG")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert res["bytes"] > 0
    #: full_page does translate into captureBeyondViewport on the protocol side
    assert mock.commands_for("Page.captureScreenshot")[0]["captureBeyondViewport"] is True


def test_pdf_writes_valid_signature(mock, client, tmp_path):
    """The PDF export writes a file with a valid %PDF signature and protects
    it at 0600 like any produced evidence."""
    out = tmp_path / "page.pdf"
    capture.pdf(client, str(out))
    #: real format signature and private permissions, just like the PNG
    assert out.read_bytes().startswith(b"%PDF")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_console_capture_normalizes_entries(mock, client):
    """The console capture aggregates raw events into normalized entries
    (kind/type/text/ts) and counts errors separately from the total volume."""
    mock.script_console(
        [
            {
                "type": "log",
                "args": [
                    {"type": "string", "value": "fixture-log"},
                    {"type": "number", "value": 42},
                ],
                "timestamp": 10.0,
            },
            {
                "type": "error",
                "args": [{"type": "string", "value": "fixture-error"}],
                "timestamp": 11.0,
            },
        ]
    )
    res = capture.console_capture(client, duration=0.3)
    #: the count distinguishes the total volume from errors alone
    assert res["count"] == 2 and res["errors"] == 1
    #: heterogeneous args (string + number) are flattened into a single timestamped text
    assert res["entries"][0] == {
        "kind": "console",
        "type": "log",
        "text": "fixture-log 42",
        "ts": 10.0,
    }


def test_console_follow_yields_ndjson_ready_entries(mock, client):
    """Follow mode produces entries directly serializable as NDJSON, in the
    same format as the one-shot capture."""
    mock.script_console(
        [
            {
                "type": "warn",
                "args": [{"type": "string", "value": "fixture-warn"}],
                "timestamp": 12.0,
            }
        ]
    )
    entries = list(capture.console_follow(client, max_entries=1))
    #: each entry in the stream is already an NDJSON line conforming to the contract
    assert entries == [{"kind": "console", "type": "warn", "text": "fixture-warn", "ts": 12.0}]


@pytest.mark.scenario(
    feature="state-session",
    journey="read-session",
    scenario_id="state-session.redact-sensitive-session-data",
    proves=["Secret, Bearer, JWT and URL credentials are absent from console output."],
)
def test_console_entries_redact_credentials_tokens_and_sensitive_urls(evidence_case):
    """No secret survives in the console output: the registered secret, the
    Bearer token, JWT and URL credentials/query are all redacted, and the
    redaction declares itself in the report."""
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123"
    context = RedactionContext.from_secrets(["registered-secret"])
    events = [
        {
            "method": "Runtime.consoleAPICalled",
            "params": {
                "type": "log",
                "args": [
                    {"value": "registered-secret"},
                    {"value": "Bearer bearer-secret"},
                    {
                        "value": (
                            "https://alice:password@example.test/callback?code=secret#fragment"
                        )
                    },
                ],
                "timestamp": 1.0,
            },
        },
        {
            "method": "Runtime.exceptionThrown",
            "params": {
                "exceptionDetails": {"exception": {"description": f"failure jwt={jwt}"}},
                "timestamp": 2.0,
            },
        },
    ]

    entries = list(capture.console_entries(events, context=context))
    serialized = json.dumps(entries)

    #: neither the registered secret value, nor the Bearer token, nor the
    #: JWT, nor the URL credentials reach the serialized output
    assert "registered-secret" not in serialized
    assert "bearer-secret" not in serialized
    assert jwt not in serialized
    assert "alice" not in serialized and "password" not in serialized
    #: the URL stays readable: only the sensitive parameter is replaced by the marker
    assert "https://example.test/callback?code=***" in entries[0]["text"]
    #: the redaction declares itself in the report, proof that it actually acted
    assert context.report.redacted is True

    # Secondary evidence: the already-redacted console output, where no canary appears.
    if evidence_case is not None:
        evidence_case.attach_json("Redacted console entries (no secret)", entries)


# -- net --------------------------------------------------------------------------


@pytest.mark.scenario(
    feature="browser-capture-observability",
    journey="inspect-runtime",
    scenario_id="browser-capture-observability.inspect-runtime-failures",
    proves=["The network capture summarizes total/failures/errors/bytes with masked URLs."],
)
def test_network_capture_assembles_requests(mock, client, evidence_case):
    """The network capture correlates request/response/finish by requestId,
    summarizes failures, HTTP errors and bytes, and masks credentials and
    tokens in every output URL."""
    navigation_url = "http://browser:password@s.test/network.html?token=navigation-secret#fragment"
    mock.script_network(
        [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R1",
                    "type": "Fetch",
                    "request": {
                        "url": "http://alice:password@s.test/api/json?token=one&token=two#part",
                        "method": "GET",
                    },
                },
            },
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": "http://alice:password@s.test/api/json?token=three#response",
                        "status": 200,
                        "mimeType": "application/json",
                    },
                },
            },
            {
                "method": "Network.loadingFinished",
                "params": {"requestId": "R1", "encodedDataLength": 123},
            },
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R2",
                    "type": "Fetch",
                    "request": {"url": "http://s.test/api/status/500", "method": "GET"},
                },
            },
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R2",
                    "response": {"status": 500, "mimeType": "application/json"},
                },
            },
            {
                "method": "Network.loadingFailed",
                "params": {"requestId": "R3", "errorText": "net::ERR_ABORTED"},
            },
        ]
    )
    res = net.capture(client, navigation_url, settle=0.2)
    #: the summary counts the network abort as a failure and the 500 as an application error
    assert res["summary"] == {"total": 3, "failed": 1, "errors_4xx_5xx": 1, "bytes": 123}
    #: the navigation URL loses its credentials, token value and fragment on output
    assert res["url"] == "http://s.test/network.html?token=***"
    r1 = next(r for r in res["requests"] if r["requestId"] == "R1")
    #: status and bytes come from the three events correlated by requestId
    assert r1["status"] == 200 and r1["encodedBytes"] == 123
    #: each request URL is masked independently of the navigation one
    assert r1["url"] == "http://s.test/api/json?token=***"
    #: navigation goes out with the raw URL: masking is an output artifact,
    #: not an alteration of browser behavior
    assert mock.commands_for("Page.navigate") == [{"url": navigation_url}]

    # Secondary evidence: the network summary and the already-masked URLs of
    # the net.capture contract, with no credential or token value.
    if evidence_case is not None:
        evidence_case.attach_json(
            "net.capture summary (masked URLs)",
            {
                "url": res["url"],
                "summary": res["summary"],
                "requests": [
                    {"requestId": r["requestId"], "url": r.get("url"), "status": r.get("status")}
                    for r in res["requests"]
                ],
            },
        )


def test_network_capture_masks_registered_secret_in_url_path(mock, client):
    """A registered secret is masked even when lodged in the URL path,
    including in its percent-encoded form."""
    secret = "reset-token-canary"
    navigation_url = f"http://s.test/reset/{secret}"
    mock.script_network(
        [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R1",
                    "type": "Fetch",
                    "request": {
                        "url": "http://s.test/api/reset%2Dtoken%2Dcanary",
                        "method": "GET",
                    },
                },
            }
        ]
    )

    result = net.capture(
        client,
        navigation_url,
        settle=0,
        context=RedactionContext.from_secrets([secret]),
    )

    serialized = json.dumps(result)
    #: the secret value cannot be found anywhere in the output, even percent-encoded
    assert secret not in serialized and "reset%2Dtoken%2Dcanary" not in serialized
    #: URLs keep their structure, only the secret segment becomes the marker
    assert result["url"] == "http://s.test/reset/***"
    assert result["requests"][0]["url"] == "http://s.test/api/***"


# -- dev loop ---------------------------------------------------------------------


def _profiler_network_script(base_url: str, headers: dict) -> list[dict]:
    return [
        {
            "method": "Network.responseReceived",
            "params": {
                "requestId": "R1",
                "response": {
                    "url": f"{base_url}/api/profiler-sim",
                    "status": 200,
                    "headers": headers,
                },
            },
        }
    ]


@pytest.mark.scenario(
    feature="dev-profiler-diff",
    journey="read-profiler",
    scenario_id="dev-profiler-diff.read-symfony-profiler",
    proves=["The profiler exposes a masked token and SQL metrics parsed from the db panel."],
)
def test_profiler_reads_debug_token_link_and_parses_panels(
    mock, client, fixtures_http, evidence_case
):
    """The profiler follows X-Debug-Token-Link, fetches the db panel in page
    context and extracts its SQL metrics, without ever letting the token
    appear in the output."""
    link = f"{fixtures_http.base_url}/_profiler/fixed-token"
    mock.on_eval("window.location.href", f"{fixtures_http.base_url}/api/profiler-sim")
    mock.script_network(
        _profiler_network_script(fixtures_http.base_url, {"X-Debug-Token-Link": link})
    )
    db_html = (FIXTURES / "profiler" / "db.html").read_text(encoding="utf-8")
    mock.on_eval(
        "__cdpx_profiler_panels",
        json.dumps([{"panel": "db", "status": 200, "html": db_html}]),
    )
    res = dev.profiler(client, f"{fixtures_http.base_url}/api/profiler-sim", panels=["db"])
    #: the token exists but only its presence is announced: no trace of its
    #: value either in the profiler URL or anywhere else in the output
    assert "token" not in res and res["token_present"] is True
    assert res["profiler_url"].endswith("/_profiler/***")
    assert "fixed-token" not in json.dumps(res)
    #: the reported status is that of the panel's actual fetch, not of the audited page
    assert res["profiler_status"] == 200  # actual status of the panel fetch
    #: the panel HTML is actually parsed: SQL requests and duplicates counted
    assert res["panels"]["db"]["queries"] == 6
    assert res["panels"]["db"]["duplicates"] == 4
    #: the output contract stays minimal, with no residual exploratory fields
    assert "signals" not in res and "profiler_bytes" not in res
    #: network listening was enabled in order to see the debug headers go by
    assert mock.commands_for("Network.enable") == [{}]
    # emitted protocol: a single page-context fetch, awaited promise
    (call,) = [
        item
        for item in mock.commands_for("Runtime.evaluate")
        if "__cdpx_profiler_panels" in item["expression"]
    ]
    #: the panel fetch is an awaited promise, targeting the token URL + panel
    assert call["awaitPromise"] is True
    assert f'"{link}?panel=db"' in call["expression"]

    # Secondary evidence: the profiler output (masked token, parsed SQL metrics).
    if evidence_case is not None:
        evidence_case.attach_json("Profiler output (masked token, db panel)", res)


def test_profiler_prefers_redirect_response_token(mock, client, fixtures_http):
    """On a 302, the token carried by redirectResponse — the only place
    Chrome exposes it — wins over the one on the followed page, and the
    reported status is that of the redirection."""
    # Chrome does not emit responseReceived for a 302: the redirection token
    # only exists in requestWillBeSent.redirectResponse and must win over the
    # one on the followed page.
    base = fixtures_http.base_url
    mock.on_eval("window.location.href", f"{base}/scenario/profiler/baseline")
    mock.script_network(
        [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R1",
                    "request": {"url": f"{base}/scenario/profiler/baseline"},
                    "redirectResponse": {
                        "url": f"{base}/scenario/profiler/routing-redirect",
                        "status": 302,
                        "headers": {"X-Debug-Token-Link": f"{base}/_profiler/redir-token"},
                    },
                },
            },
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": f"{base}/scenario/profiler/baseline",
                        "status": 200,
                        "headers": {"X-Debug-Token-Link": f"{base}/_profiler/final-token"},
                    },
                },
            },
        ]
    )
    res = dev.profiler(client, f"{base}/scenario/profiler/routing-redirect", panels=[])
    #: a token was indeed retained, and neither the redirection one nor the
    #: final page one leaks in clear text in the output
    assert "token" not in res and res["token_present"] is True
    assert res["profiler_url"].endswith("/_profiler/***")
    assert "redir-token" not in json.dumps(res)
    assert "final-token" not in json.dumps(res)
    #: the reported status is that of the intercepted redirection, not the final 200
    assert res["status"] == 302


def test_profiler_falls_back_to_debug_token(mock, client, fixtures_http):
    """Without X-Debug-Token-Link, the X-Debug-Token header is enough: the
    profiler URL is reconstructed and the header itself is masked in the
    output."""
    mock.on_eval("window.location.href", f"{fixtures_http.base_url}/api/profiler-sim")
    mock.script_network(
        _profiler_network_script(fixtures_http.base_url, {"X-Debug-Token": "fixed-token"})
    )
    res = dev.profiler(client, f"{fixtures_http.base_url}/api/profiler-sim", panels=[])
    #: presence reported without disclosing the token: URL masked, header
    #: masked, no occurrence of the value in the serialized output
    assert "token" not in res and res["token_present"] is True
    assert res["profiler_url"].endswith("/_profiler/***")
    assert res["response_headers"]["x-debug-token"] == "***"
    assert "fixed-token" not in json.dumps(res)
    # token-only probe: no panel fetch
    #: with no panel requested, no page-context fetch is even attempted
    assert res["panels"] == {} and res["profiler_status"] is None
    assert not any(
        "__cdpx_profiler_panels" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_profiler_rejects_requested_origin_before_navigation(mock, client):
    """An origin outside the allowlist is rejected before enabling network
    or navigating: the guard operates upstream of any protocol."""
    #: the refusal is an explicit ValueError, not a navigation aborted mid-flight
    with pytest.raises(ValueError, match="origin rejected"):
        dev.profiler(
            client,
            "https://attacker.example/report",
            panels=["db"],
            context=orchestration("http://allowed.test"),
        )

    #: proof of fail-closed: no CDP command went out to the browser
    assert mock.commands_for("Network.enable") == []
    assert mock.commands_for("Page.navigate") == []


def test_profiler_rejects_cross_origin_header_before_panel_fetch(mock, client):
    """An X-Debug-Token-Link pointing to a foreign origin is treated as
    hostile: refusal before any panel fetch."""
    url = "http://allowed.test/report"
    mock.on_eval("window.location.href", url)
    mock.script_network(
        _profiler_network_script(
            "http://allowed.test",
            {"X-Debug-Token-Link": "https://attacker.example/_profiler/stolen"},
        )
    )

    #: the header forged by the server triggers the origin refusal
    with pytest.raises(ValueError, match="origin rejected"):
        dev.profiler(client, url, panels=["db"])

    #: no panel fetch was attempted toward the foreign origin
    assert not any(
        "__cdpx_profiler_panels" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_profiler_rejects_forbidden_final_url_before_panel_fetch(mock, client):
    """Even when the requested URL is allowed, a redirection to a forbidden
    origin blocks the profiler before the panel fetch: the real destination
    is what counts."""
    requested = "http://allowed.test/report"
    mock.on_eval("window.location.href", "https://attacker.example/redirected")
    mock.script_network(
        _profiler_network_script(
            "http://allowed.test",
            {"X-Debug-Token-Link": "http://allowed.test/_profiler/token"},
        )
    )

    #: it is the final URL after redirection that is judged, not the requested one
    with pytest.raises(ValueError, match="origin rejected"):
        dev.profiler(
            client,
            requested,
            panels=["db"],
            context=orchestration("http://allowed.test"),
        )

    #: no panel was fetched from the hijacked page
    assert not any(
        "__cdpx_profiler_panels" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_dom_diff_runs_action_and_returns_unified_diff(mock, client):
    """dom-diff actually executes the action between two snapshots and
    returns a diff that localizes the DOM mutation."""
    before = ["<body>", '  <div#result[data-state="idle"]>']
    after = ["<body>", '  <div#result[data-state="submitted"]>', '    "OK:Léo"']
    mock.on_eval("__cdpx_dom_snapshot", json.dumps(before), json.dumps(after))
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    res = dev.dom_diff(client, ClickAction("#submit-btn"))
    #: the diff detects the mutation and exposes the changed line, usable as-is
    assert res["changed"] is True
    assert any("submitted" in line for line in res["diff"])
    #: the action was not simulated: the full mouse sequence really went out
    assert [m["type"] for m in mock.commands_for("Input.dispatchMouseEvent")] == [
        "mouseMoved",
        "mousePressed",
        "mouseReleased",
    ]


def test_dom_diff_is_stable_across_runs_on_same_state(mock, client):
    """Two runs of dom-diff on an identical DOM state produce a strictly
    identical diff: the output is deterministic, not run-dependent."""
    before = ["<body>", '  <div#result[data-state="idle"]>']
    after = ["<body>", '  <div#result[data-state="submitted"]>', '    "OK:Léo"']
    #: the mock replays exactly the same before/after pair for each run
    mock.on_eval(
        "__cdpx_dom_snapshot",
        json.dumps(before),
        json.dumps(after),
        json.dumps(before),
        json.dumps(after),
    )
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    first = dev.dom_diff(client, ClickAction("#submit-btn"))
    second = dev.dom_diff(client, ClickAction("#submit-btn"))

    #: the diff is strictly identical from one run to the next, line by line
    assert first["diff"] == second["diff"]
    #: stability covers the whole output, not just the diff field
    assert first == second
    #: guard: the compared diff is not trivially empty
    assert first["changed"] is True
    assert first["lines"] > 0


# -- state ------------------------------------------------------------------------


def test_cookies_masked_by_default(mock, client):
    """Cookie values are masked by default; reading them in clear text
    requires the explicit show_values opt-in."""
    res = state.get_cookies(client)
    #: by default the output declares itself masked and shows only the marker
    assert res["values_masked"] is True
    assert res["cookies"][0]["value"] == "***"
    res2 = state.get_cookies(client, show_values=True)
    #: the opt-in reveals the real value: masking is not destructive
    assert res2["cookies"][0]["value"] == "secret-session-token"


def test_set_and_clear_cookies(mock, client):
    """set_cookie really writes into the browser's cookie jar and
    clear_cookies empties it via the modern Storage API."""
    state.set_cookie(client, "flag", "1", "http://127.0.0.1/")
    #: the set cookie is visible on the browser side, not merely accepted by the API
    assert any(c["name"] == "flag" for c in mock.cookies)
    res = state.clear_cookies(client)
    #: the purge announces the method used and the jar is actually empty
    assert res["method"] == "Storage.clearCookies"
    assert mock.cookies == []


def test_clear_cookies_falls_back_to_network_method(mock, client):
    """When Storage.clearCookies fails, the Network-domain fallback succeeds
    and the output announces the method actually used."""
    # Supported endpoint exposing cookie clearing through Network.
    mock.fail_on("Storage.clearCookies")
    res = state.clear_cookies(client)
    #: the output tells the truth: it is the fallback method that cleaned up
    assert res == {"cleared": True, "method": "Network.clearBrowserCookies"}
    #: the order proves the modern attempt happens first, the fallback only after
    assert [m for (_t, m, _p) in mock.commands] == [
        "Storage.clearCookies",
        "Network.clearBrowserCookies",
    ]
    #: the fallback actually emptied the jar, not just returned a status
    assert mock.cookies == []


def test_get_storage_masks_values_by_default_with_explicit_opt_in(mock, client):
    """Storage is masked by default (values replaced, values_masked flag
    raised) and only show_values=True delivers the real data."""
    secret = "storage-secret-value"
    mock.on_eval("localStorage", json.dumps({"cdpx-key": secret}))
    res = state.get_storage(client, "local")
    shown = state.get_storage(client, "local", show_values=True)

    #: the default read counts the entries but replaces every value, and the
    #: secret value is absent from the entire serialized output
    assert res == {
        "kind": "local",
        "entries": {"cdpx-key": "***"},
        "count": 1,
        "values_masked": True,
    }
    assert secret not in json.dumps(res)
    #: the opt-in returns the same keys with clear-text values, flag lowered
    assert shown == {
        "kind": "local",
        "entries": {"cdpx-key": secret},
        "count": 1,
        "values_masked": False,
    }
    #: two reads = two distinct evaluations, no implicit caching
    assert len(mock.commands_for("Runtime.evaluate")) == 2


def test_storage_rejects_unknown_kind_before_evaluation(mock, client):
    with pytest.raises(ValueError, match="unknown storage"):
        state.get_storage(client, "persistent")  # type: ignore[arg-type]

    assert mock.commands == []


# -- audit ------------------------------------------------------------------------

SEO_OK = {
    "url": "http://127.0.0.1/seo.html",
    "lang": "fr",
    "title": "SEO fixture — compliant page",
    "metas": {"description": "ok", "robots": "index,follow"},
    "canonical": "http://127.0.0.1/seo.html",
    "robots": "index,follow",
    "h1": ["Compliant unique H1"],
    "hreflang": [{"lang": "fr", "href": "/seo.html"}, {"lang": "en", "href": "/en/seo.html"}],
    "jsonld": [{"@type": "Product", "sku": "FIX-001"}],
    "images_without_alt": 0,
    "links": {"internal": 1, "external": 1, "nofollow": 1},
}

SEO_BROKEN = {
    "url": "http://127.0.0.1/seo-broken.html",
    "lang": None,
    "title": "",
    "metas": {},
    "canonical": None,
    "robots": None,
    "h1": ["First H1", "Second H1 (error)"],
    "hreflang": [],
    "jsonld": [],
    "images_without_alt": 2,
    "links": {"internal": 0, "external": 0, "nofollow": 0},
}


def test_seo_clean_page_no_findings(mock, client):
    """A compliant SEO page produces no finding — zero false positives —
    while still delivering informative data (title width, JSON-LD)."""
    mock.on_eval("__cdpx_seo", json.dumps(SEO_OK))
    res = audit.seo(client)
    #: no false positive on the compliant reference page
    assert res["findings"] == []
    #: informative metrics are still provided even with no problem detected
    assert res["title_px_estimate"] > 0
    assert res["jsonld"][0]["@type"] == "Product"


def test_seo_broken_page_findings(mock, client):
    """Every major SEO defect on the broken page produces its own named
    finding: title, description, canonical, multiple h1s and images without
    alt."""
    mock.on_eval("__cdpx_seo", json.dumps(SEO_BROKEN))
    res = audit.seo(client)
    #: each shortcoming is labeled in clear text, directly usable from the CLI
    assert "missing title" in res["findings"]
    assert "missing meta description" in res["findings"]
    assert "missing canonical" in res["findings"]
    assert "2 h1 (expected: 1)" in res["findings"]
    assert "2 image(s) without alt" in res["findings"]


def test_seo_advanced_findings(mock, client):
    """The audit detects subtle cases: duplicate h1s (case-folded
    comparison), malformed JSON-LD and incomplete Product."""
    payload = {
        **SEO_OK,
        "h1": ["Same", "Same"],
        "jsonld": [{"@type": "Product"}, {"__parse_error": "SyntaxError"}],
        "images_without_alt": 1,
    }
    mock.on_eval("__cdpx_seo", json.dumps(payload))
    res = audit.seo(client)
    #: duplicates are spotted regardless of case, and JSON-LD is judged on
    #: both its syntactic validity AND its business completeness
    assert "duplicate h1: same" in res["findings"]
    assert "invalid JSON-LD" in res["findings"]
    assert "incomplete Product JSON-LD (sku or name required)" in res["findings"]


def test_seo_accepts_top_level_jsonld_arrays_and_reports_scalars(mock, client):
    """A JSON-LD script containing a top-level array is unfolded and
    audited object by object; a scalar is reported without crashing the
    audit."""
    payload = {
        **SEO_OK,
        "jsonld": [
            [{"@type": "Product", "name": "Valid"}, {"@type": "Product"}],
            "not-an-object",
        ],
    }
    mock.on_eval("__cdpx_seo", json.dumps(payload))
    res = audit.seo(client)
    #: the incomplete object nested in the array is found, and the scalar
    #: produces a finding instead of an exception
    assert res["findings"] == [
        "incomplete Product JSON-LD (sku or name required)",
        "unsupported scalar JSON-LD",
    ]


def test_metrics(mock, client):
    """Performance domain metrics are flattened under their original name,
    numeric values intact."""
    res = audit.metrics(client)
    #: the protocol's counters pass through without conversion or renaming
    assert res["Nodes"] == 42 and res["JSHeapUsedSize"] == 1048576


# -- interception, emulation, diagnostics, recording --------------------------


def test_intercept_goto_fulfills_matching_request(mock, client):
    """A '=> 503' rule artificially fulfills the intercepted request with
    that status and logs the corresponding hit."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {
                    "requestId": "I1",
                    "request": {"url": "http://s.test/api/payment", "method": "POST"},
                },
            }
        ]
    )
    res = interception.intercept_goto(
        client,
        "http://s.test/checkout",
        rules=["*payment* => 503"],
    )
    #: the logged hit links the intercepted URL to the applied rule's action
    assert res["hits"] == [{"url": "http://s.test/api/payment", "action": "503"}]
    #: the request was fulfilled on the protocol side with the simulated status
    assert mock.commands_for("Fetch.fulfillRequest")[0]["responseCode"] == 503


def test_intercept_goto_blocks_and_continues(mock, client):
    """Every intercepted request gets its own decision: the rule blocks A
    while B, with no matching rule, continues by default."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {"requestId": "A", "request": {"url": "http://s.test/a"}},
            },
            {
                "method": "Fetch.requestPaused",
                "params": {"requestId": "B", "request": {"url": "http://s.test/b"}},
            },
        ]
    )
    res = interception.intercept_goto(client, "http://s.test/", rules=["*a => block"])
    #: the log distinguishes the targeted block from the default continuation
    assert res["hits"] == [
        {"url": "http://s.test/a", "action": "block"},
        {"url": "http://s.test/b", "action": "continue"},
    ]
    #: on the protocol side, A fails and B continues — both decisions are explicit
    assert mock.commands_for("Fetch.failRequest")[0]["requestId"] == "A"
    assert mock.commands_for("Fetch.continueRequest")[0]["requestId"] == "B"


@pytest.mark.parametrize(
    "rule",
    [
        "broken",
        "=> block",
        "* =>",
        "* => typo",
        "* => Continue",
        "* => 199",
        "* => 600",
        "* => 200.0",
    ],
)
def test_intercept_rejects_invalid_rule_before_cdp(mock, client, rule):
    """Any syntactically invalid rule (missing pattern, unknown action,
    out-of-bounds or non-integer status) is rejected before any CDP exchange
    whatsoever."""
    #: the rule grammar is validated statically, whatever the specific defect
    with pytest.raises(ValueError):
        interception.intercept_goto(client, "http://s.test/", rules=[rule])
    #: fail-closed: the browser saw nothing go through
    assert mock.commands == []


def test_intercept_prevalidates_every_rule_before_cdp(mock, client):
    """An invalid rule in second position condemns the whole batch: every
    rule is validated before the first CDP command."""
    #: a valid rule in first position is not enough to start the interception
    with pytest.raises(ValueError):
        interception.intercept_goto(
            client,
            "http://s.test/",
            rules=["*first* => continue", "*second* => typo"],
        )
    #: no partial command: it's all or nothing
    assert mock.commands == []


@pytest.mark.parametrize(
    ("operation", "kwargs"),
    [
        (capture.console_capture, {"duration": -0.1}),
        (net.capture, {"url": "http://s.test/", "settle": -0.1}),
        (dev.profiler, {"url": "http://s.test/", "settle": -0.1}),
        (diagnostics.vitals, {"url": "http://s.test/", "settle": -0.1}),
        (
            interception.intercept_goto,
            {"rules": [], "url": "http://s.test/", "settle": -0.1},
        ),
    ],
)
def test_event_primitives_reject_negative_budgets_before_cdp(mock, client, operation, kwargs):
    with pytest.raises(ValueError):
        operation(client, **kwargs)

    assert mock.commands == []


def test_intercept_zero_timeout_is_immediate_and_uses_cdp_timeout(mock, client):
    with pytest.raises(CDPTimeout, match="interception timeout"):
        interception.intercept_goto(
            client,
            "http://s.test/",
            rules=[],
            timeout=0,
            settle=0,
        )

    assert mock.commands == []


@pytest.mark.parametrize("status", [200, 599])
def test_intercept_accepts_status_bounds(mock, client, status):
    """Statuses at the bounds of the accepted domain (200 and 599) pass
    validation and are actually applied to the intercepted request."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {
                    "requestId": "I1",
                    "request": {"url": "http://s.test/api/status"},
                },
            }
        ]
    )
    res = interception.intercept_goto(
        client,
        "http://s.test/",
        rules=[f"*status* => {status}"],
        settle=0,
    )
    #: the boundary status carries through to the protocol's fulfillRequest, unrounded
    assert res["hits"] == [{"url": "http://s.test/api/status", "action": str(status)}]
    assert mock.commands_for("Fetch.fulfillRequest")[0]["responseCode"] == status


def test_intercept_accepts_explicit_continue(mock, client):
    """The explicit 'continue' action lets the intercepted request through
    while still logging it as a hit."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {
                    "requestId": "I1",
                    "request": {"url": "http://s.test/api/continue"},
                },
            }
        ]
    )
    res = interception.intercept_goto(
        client,
        "http://s.test/",
        rules=["*continue* => continue"],
        settle=0,
    )
    #: the hit is logged even when the rule lets the request through
    assert res["hits"] == [{"url": "http://s.test/api/continue", "action": "continue"}]
    #: the request continues on its way via continueRequest, unaltered
    assert mock.commands_for("Fetch.continueRequest") == [{"requestId": "I1"}]


def test_emulate_mobile_and_reset(mock, client):
    """Mobile emulation applies the device override, and the reset restores
    every dimension, including the UA, via the complete protocol sequence."""
    #: the mobile profile is applied with the matching device flag
    assert emulation.emulate(client, "mobile")["applied"] is True
    assert mock.commands_for("Emulation.setDeviceMetricsOverride")[0]["mobile"] is True
    mock.commands.clear()
    assert emulation.emulate(client, reset=True)["reset"] is True
    # Full reset sequence. setUserAgentOverride "" restores the default UA
    # and clearDeviceMetricsOverride lifts the device override.
    #: the reset sequence covers device, UA, network and CPU — nothing is forgotten
    assert [m for (_t, m, _p) in mock.commands] == [
        "Emulation.clearDeviceMetricsOverride",
        "Emulation.setUserAgentOverride",
        "Network.emulateNetworkConditions",
        "Emulation.setCPUThrottlingRate",
    ]
    #: empty UA = back to Chrome's default UA; rate 1 = CPU unthrottled
    assert mock.commands_for("Emulation.setUserAgentOverride")[0] == {"userAgent": ""}
    assert mock.commands_for("Emulation.setCPUThrottlingRate")[0] == {"rate": 1}


def test_vitals_installs_observer_and_reads_values(mock, client):
    """vitals installs the observer before navigation, triggers the
    requested interaction and then reads the web vitals metrics from the
    page."""
    mock.on_eval("__cdpxVitals", json.dumps({"lcp": 12, "cls": 0.1, "inp": 0}))
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    res = diagnostics.vitals(client, "http://s.test/vitals.html", click_selector="#inp-button")
    #: the reported metrics really come from the observer injected into the page
    assert res["lcp"] == 12 and res["cls"] == 0.1
    #: the observer was in place before the load, and the INP click did happen
    assert mock.commands_for("Page.addScriptToEvaluateOnNewDocument")
    assert mock.commands_for("Input.dispatchMouseEvent")


def test_vitals_click_accepts_explicit_default_port_in_current_origin(mock, client):
    """Characterization of the vitals click's canonical origin guard: a
    current URL carrying an explicit default port (:80) is accepted against
    http://*.test — a deliberate divergence from the old fnmatch matcher,
    which used to refuse it, now pinned."""
    mock.on_eval("window.location.href", "http://s.test:80/vitals.html")
    mock.on_eval("__cdpxVitals", json.dumps({"lcp": 5, "cls": 0.0, "inp": 1}))
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    res = diagnostics.vitals(
        client,
        "http://s.test/vitals.html",
        click_selector="#go",
        origins="http://*.test",
    )
    #: the policy's canonical matcher normalizes the default port: click allowed
    assert mock.commands_for("Input.dispatchMouseEvent")
    assert res["inp"] == 1


def test_profiler_fails_fast_on_navigation_error(mock, client, monkeypatch):
    """Characterization of the profiler's fail-fast behavior: an errorText
    from Page.navigate fails immediately, without waiting for loadEventFired
    until the timeout as the old behavior used to — a deliberate divergence,
    pinned here."""
    real_send = client.send

    def send(method, params=None, **kwargs):
        if method == "Page.navigate":
            return {"frameId": "F1", "errorText": "ERR_CONNECTION_REFUSED"}
        return real_send(method, params, **kwargs)

    monkeypatch.setattr(client, "send", send)
    with pytest.raises(nav.NavigationError, match="ERR_CONNECTION_REFUSED"):
        dev.profiler(client, "http://s.test/page")
    #: the failure precedes any reading of the page: no JS evaluation emitted
    assert mock.commands_for("Runtime.evaluate") == []


def test_vitals_rechecks_redirected_origin_before_click(mock, client):
    """Even after an allowed navigation, a redirection outside the permitted
    origins blocks the INP click: the mutation is re-judged against the real
    URL."""
    mock.on_eval("window.location.href", "https://prod.example/redirected")
    #: the mutation is refused based on the real destination, not the requested URL
    with pytest.raises(ValueError, match="origin rejected"):
        diagnostics.vitals(
            client,
            "http://allowed.test/vitals.html",
            click_selector="#go",
            origins="http://*.test",
        )
    #: no click was emitted toward the hijacked page
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_a11y_compacts_ax_tree(mock, client):
    """The accessibility tree is compacted into a flat list of nodes that
    keep their role usable for the audit."""
    res = diagnostics.a11y(client)
    #: compaction preserves the node count and their meaningful roles
    assert res["count"] == 2
    assert res["nodes"][1]["role"] == "button"


def test_coverage_aggregates_files(mock, client):
    """Coverage aggregates JS and CSS per file under a stable contract; with
    no measurable data, the percentage is None rather than a false zero."""
    res = diagnostics.coverage(client, "http://s.test/")
    #: the per-file contract is complete and the indeterminate stays None, not 0
    assert res["files"][0] == {
        "url": "http://fixture/app.js",
        "functions": 1,
        "used_ranges": 0,
        "total_bytes": 0,
        "used_bytes": 0,
        "unused_bytes": 0,
        "coverage_percent": None,
    }
    #: the JS and CSS aggregates separate used from unused
    assert res["js"] == {"total_bytes": 0, "used_bytes": 0, "unused_bytes": 0}
    assert res["css"] == {"rules": 2, "used": 1, "unused": 1}


def test_coverage_reports_byte_coverage_not_range_counts(mock, client, monkeypatch):
    """JS coverage is measured in bytes actually executed — dead ranges
    subtracted, overlaps deduced — not in the number of ranges."""
    original_send = client.send

    def send(method, params=None, timeout=None):
        if method == "Profiler.takePreciseCoverage":
            return {
                "result": [
                    {
                        "url": "http://fixture/app.js",
                        "functions": [
                            {"ranges": [{"startOffset": 0, "endOffset": 100, "count": 1}]},
                            {"ranges": [{"startOffset": 20, "endOffset": 40, "count": 0}]},
                        ],
                    }
                ]
            }
        return original_send(method, params, timeout)

    monkeypatch.setattr(client, "send", send)
    res = diagnostics.coverage(client, "http://s.test/")
    #: 100 bytes with 20 dead give 80%: the calculation subtracts the
    #: unexecuted ranges instead of counting the ranges
    assert res["js"] == {"total_bytes": 100, "used_bytes": 80, "unused_bytes": 20}
    assert res["files"][0]["coverage_percent"] == 80.0


def test_frame_text_reads_iframe_content(mock, client):
    """frame_text reads the text inside an iframe via its contentDocument,
    out of reach of a querySelector on the host page."""
    mock.on_eval("contentDocument", "iframe text")
    #: the returned text comes from the embedded document, not the host page
    assert frames.frame_text(client, "#child-marker")["text"] == "iframe text"


def test_record_executes_action_and_journals_result(mock, client, tmp_path):
    """record actually executes the action (protocol emitted) and journals
    the complete NDJSON event: action, outcome and structured result."""
    path = tmp_path / "record.ndjson"
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    res = recording.record(client, str(path), ClickAction("#submit"), context=orchestration())
    #: the summary reports a success and exactly one journaled event
    assert res["ok"] is True and res["recorded"] == 1
    # the action was actually executed (protocol emitted), not merely journaled
    #: proof of real execution: the complete mouse sequence really went out to the page
    assert [m["type"] for m in mock.commands_for("Input.dispatchMouseEvent")] == [
        "mouseMoved",
        "mousePressed",
        "mouseReleased",
    ]
    event = json.loads(path.read_text().splitlines()[0])
    #: the NDJSON journal keeps the action, its outcome and its result, the basis for replay
    assert event["action"] == ["click", "#submit"]
    assert event["ok"] is True
    assert event["result"]["clicked"] == "#submit"


def test_record_journals_failure_then_raises(mock, client, tmp_path):
    """An action failure is first journaled (ok=false + error) and then the
    exception propagates up to the CLI: the journal never loses the
    failure."""
    path = tmp_path / "record.ndjson"
    mock.on_eval("getBoundingClientRect", None)  # element not found
    #: the failure propagates as a typed exception, after the journal is written
    with pytest.raises(inputs.ElementNotFound):
        recording.record(client, str(path), ClickAction("#missing"), context=orchestration())
    event = json.loads(path.read_text().splitlines()[0])
    #: the trace keeps the attempted action, its failure and the error message
    assert event["ok"] is False and event["action"] == ["click", "#missing"]
    assert "#missing" in event["result"]["error"]


def test_replay_reexecutes_journal_against_browser(mock, client, tmp_path):
    """replay re-executes every journal event against the browser, in
    recording order, and reports a faithful, complete replay."""
    path = tmp_path / "record.ndjson"
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    recording.record(client, str(path), GotoAction("http://site.test/"), context=orchestration())
    recording.record(client, str(path), ClickAction("#submit"), context=orchestration())
    mock.commands.clear()
    res = recording.replay(client, str(path), context=orchestration())
    #: the summary attests two events read, two played, no divergence
    assert res == {"path": str(path), "events": 2, "played": 2, "ok": True}
    # the replay really re-emitted navigation then click, in journal order
    methods = [m for (_t, m, _p) in mock.commands]
    #: the order of the re-emitted protocol respects the journal order
    assert methods.index("Page.navigate") < methods.index("Input.dispatchMouseEvent")


def test_replay_rejects_v1_type_without_exposing_text(client, tmp_path, monkeypatch):
    """A v1 journal containing clear-text typed text is refused without
    being replayed and without the inherited sensitive value leaking into
    the summary."""
    path = tmp_path / "schema-v1-type.ndjson"
    path.write_text(
        '{"action":["type","#name","schema-v1-secret"],"ok":true,'
        '"result":{"typed":"schema-v1-secret","selector":"#name","cleared":false}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        recording.actions,
        "run_action",
        lambda _client, _action: {
            "typed": True,
            "value_masked": True,
            "selector": "#name",
            "cleared": False,
        },
    )

    result = recording.replay(client, str(path), context=orchestration())

    #: clean refusal: no action played on a sensitive archive format
    assert result["ok"] is False and result["played"] == 0
    #: the refusal is justified and the inherited secret value appears nowhere
    assert "sensitive v1 action refused" in result["divergence"]
    assert "schema-v1-secret" not in json.dumps(result)


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="replay-flow",
    scenario_id="orchestration-control.orchestrate-replay-and-emulation",
    proves=["The replay stops dead at the first divergence, without replaying what follows."],
)
def test_replay_stops_at_first_divergence(mock, client, tmp_path, evidence_case):
    """The replay stops dead at the first divergence: the offending event is
    identified and the following events are never executed."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://site.test/"],"ok":true}\n'
        '{"action":["click","#gone"],"ok":true}\n'
        '{"action":["goto","http://after.test/"],"ok":true}\n',
        encoding="utf-8",
    )
    mock.on_eval("getBoundingClientRect", None)  # the replayed click fails
    res = recording.replay(client, str(path), context=orchestration())
    #: only one action played before the failure, divergence localized to the offender
    assert res["ok"] is False and res["played"] == 1
    assert res["divergence"].startswith("event 1:")
    # clean stop: the next journal action was not replayed
    #: the navigation that followed the offending click was never re-emitted
    assert [p.get("url") for p in mock.commands_for("Page.navigate")] == ["http://site.test/"]

    # Secondary evidence: the replayed NDJSON journal (typed as logs) and the
    # divergence object that documents the clean stop at the first mismatch.
    if evidence_case is not None:
        evidence_case.attach_file(path, "Replayed record.ndjson journal")
        evidence_case.attach_json(
            "Replay divergence (clean stop)",
            {"ok": res["ok"], "played": res["played"], "divergence": res["divergence"]},
        )


def test_replay_divergence_on_journaled_failure(mock, client, tmp_path):
    """An event journaled as a failure is an immediate divergence: it does
    not get replayed and nothing goes out to the browser."""
    path = tmp_path / "record.ndjson"
    path.write_text('{"action":["click","#submit"],"ok":false}\n', encoding="utf-8")
    res = recording.replay(client, str(path), context=orchestration())
    #: the divergence cites the journaled failure without having tried to reproduce it
    assert res["ok"] is False and res["divergence"] == "event 0: ok=false journaled"
    #: fail-closed: no CDP command was emitted
    assert mock.commands == []  # a failed record is never replayed


def test_replay_validates_journal_before_any_execution(mock, client, tmp_path):
    """The entire journal is validated (JSON, action present, max_actions
    budget) before any execution whatsoever: any corruption blocks
    everything."""
    path = tmp_path / "record.ndjson"
    path.write_text('{"action":["goto","http://x.test/"],"ok":true}\n{not-json}\n', "utf-8")
    res = recording.replay(client, str(path), context=orchestration())
    #: the corrupted line is localized and even the first valid line is not
    #: replayed
    assert res["ok"] is False and res["divergence"].startswith("line 2:")
    assert mock.commands == []  # corrupted journal -> nothing is replayed
    path.write_text('{"ok":true}\n', encoding="utf-8")
    #: an entry without an action is an invalid journal, reported by its line
    assert (
        recording.replay(client, str(path), context=orchestration())["divergence"]
        == "line 1: missing action"
    )
    path.write_text('{"action":["goto","http://x.test/"],"ok":true}\n' * 3, encoding="utf-8")
    #: exceeding the action budget raises before any protocol emission
    with pytest.raises(ValueError):
        recording.replay(client, str(path), max_actions=2, context=orchestration())
    assert mock.commands == []  # budget exceeded -> nothing is replayed


def test_replay_validates_action_grammar_before_any_execution(mock, client, tmp_path):
    """A verb outside the grammar in the journal is refused at validation,
    before even the first action — however valid — gets replayed."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://x.test/"],"ok":true,"result":{"ok":true}}\n'
        '{"action":["shell","oops"],"ok":true,"result":{}}\n',
        encoding="utf-8",
    )
    res = recording.replay(client, str(path), context=orchestration())
    #: the forbidden verb is spotted at its line and nothing was executed, not
    #: even the otherwise valid first action
    assert res["ok"] is False and res["divergence"].startswith("line 2:")
    assert mock.commands == []


def test_replay_compares_semantic_results(mock, client, tmp_path):
    """The replay compares results semantically: a diverging significant
    field is reported with path/expected/actual, while volatile fields like
    elapsed_ms are ignored."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://x.test/"],"ok":true,'
        '"result":{"url":"http://other.test/","ok":true,"elapsed_ms":999}}\n',
        encoding="utf-8",
    )
    res = recording.replay(client, str(path), context=orchestration())
    #: the structured divergence cites only the significant field (url), not
    #: the volatile timing, even though it differs too
    assert res["ok"] is False and res["played"] == 1
    assert res["divergence"] == {
        "event": 0,
        "kind": "result_mismatch",
        "differences": [
            {"path": "$.url", "expected": "http://other.test/", "actual": "http://x.test/"}
        ],
    }


def test_replay_origin_guard_follows_goto_before_mutation(mock, client, tmp_path):
    """The origin guard follows the journal's navigation: a goto to a
    forbidden origin blocks the replay before the mutation that follows."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://prod.example/"],"ok":true}\n'
        '{"action":["click","#submit"],"ok":true}\n',
        encoding="utf-8",
    )
    mock.on_eval("window.location.href", "http://prod.example/")
    res = recording.replay(client, str(path), context=orchestration())
    #: the forbidden navigation blocks the replay before even the first action
    assert res["ok"] is False and res["played"] == 0
    #: the refusal is justified by the origin and the click never went out
    assert "origin rejected" in str(res["divergence"])
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_replay_origin_guard_uses_redirect_destination_before_mutation(mock, client, tmp_path):
    """It is the real destination after redirection that gets judged: an
    allowed goto that ends up outside the zone blocks the following mutation
    as soon as the URL is first read."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://allowed.test/start"],"ok":true}\n'
        '{"action":["click","#submit"],"ok":true}\n',
        encoding="utf-8",
    )
    mock.on_eval("window.location.href", "https://prod.example/redirected")

    res = recording.replay(client, str(path), context=orchestration())

    #: the goto did play but the mutation that follows is refused
    assert res["ok"] is False and res["played"] == 1
    #: origin refused: the mouse stays silent facing the hijacked page
    assert "origin rejected" in str(res["divergence"])
    assert mock.commands_for("Input.dispatchMouseEvent") == []
    location_reads = [
        params
        for params in mock.commands_for("Runtime.evaluate")
        if params["expression"] == "window.location.href"
    ]
    #: a single URL read was enough: the refusal is immediate, never retried
    assert len(location_reads) == 1  # real destination refused immediately after goto


def test_replay_rejects_forbidden_goto_before_navigation(mock, client, tmp_path):
    """A journal goto to a forbidden origin is refused before navigating:
    the guard also covers actions that move the context."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","https://forbidden.example/"],"ok":true}\n',
        encoding="utf-8",
    )

    result = recording.replay(
        client,
        str(path),
        context=orchestration("http://allowed.test"),
    )

    #: replay refused with zero actions played, justified by the forbidden origin
    assert result["ok"] is False and result["played"] == 0
    assert "origin rejected" in result["divergence"]
    #: the forbidden navigation was never emitted toward the browser
    assert mock.commands_for("Page.navigate") == []


def test_record_rejects_forbidden_goto_before_navigation_or_journal(mock, client, tmp_path):
    """record refuses a goto outside the origins before navigating AND
    before opening the journal: a forbidden action leaves no artifact
    behind."""
    path = tmp_path / "record.ndjson"

    #: the prohibition kicks in upstream of any side effect
    with pytest.raises(ValueError, match="origin rejected"):
        recording.record(
            client,
            str(path),
            GotoAction("https://forbidden.example/"),
            context=orchestration("http://allowed.test"),
        )

    #: no navigation emitted, no journal file created on disk
    assert mock.commands_for("Page.navigate") == []
    assert not path.exists()


@pytest.mark.parametrize(
    ("events", "played"),
    [
        ('{"action":["click","#submit"],"ok":true}\n', 0),
        (
            '{"action":["goto","http://allowed.test/start"],"ok":true}\n'
            '{"action":["click","#submit"],"ok":true}\n',
            1,
        ),
    ],
)
def test_replay_origin_guard_fails_closed_when_current_url_is_unknown(
    mock, client, tmp_path, events, played
):
    """When the current URL cannot be determined, the guard fails closed:
    the mutation is refused, whether or not the journal starts with an
    allowed goto."""
    path = tmp_path / "record.ndjson"
    path.write_text(events, encoding="utf-8")
    mock.on_eval("window.location.href", None)

    res = recording.replay(client, str(path), context=orchestration())

    #: the replay stops exactly where the URL becomes necessary for the judgment
    assert res["ok"] is False and res["played"] == played
    #: the reason is indeterminacy, and no click went out blindly
    assert "unable to determine the current URL" in str(res["divergence"])
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_replay_origin_guard_is_kept_after_mutation(mock, client, tmp_path):
    """The guard stays active after every mutation: a post-click redirection
    outside the allowed zone is detected and reported as a divergence."""
    path = tmp_path / "record.ndjson"
    path.write_text('{"action":["click","#submit"],"ok":true}\n', encoding="utf-8")
    mock.on_eval(
        "window.location.href",
        "http://allowed.test/form",
        "https://prod.example/redirected",
    )
    mock.on_eval(
        "getBoundingClientRect",
        json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}),
    )

    res = recording.replay(client, str(path), context=orchestration())

    #: the click did play, then the resulting destination was refused
    assert res["ok"] is False and res["played"] == 1
    assert "destination after action: origin rejected" in str(res["divergence"])
    #: the mutation really had taken place (complete mouse sequence) before detection
    assert len(mock.commands_for("Input.dispatchMouseEvent")) == 3


def test_run_action_dispatches_and_rejects_unknown(mock, client):
    """run_action routes every known verb to its primitive — protocol proof
    included — and rejects every verb outside the grammar, including an
    empty action."""

    res = actions.run_action_argv(client, ["goto", "http://site.test/"])
    #: the goto verb reaches the real navigation primitive, protocol emitted
    assert res["ok"] is True
    assert mock.commands_for("Page.navigate") == [{"url": "http://site.test/"}]
    mock.on_eval("2 + 2", 4)
    #: the eval verb returns the value computed by the page
    assert actions.run_action(client, EvalAction("2 + 2")) == {"value": 4}
    #: an unknown verb and an empty action are both refused by the grammar
    with pytest.raises(ValueError):
        actions.run_action_argv(client, ["shell", "rm -rf /"])
    with pytest.raises(ValueError):
        actions.run_action_argv(client, [])
