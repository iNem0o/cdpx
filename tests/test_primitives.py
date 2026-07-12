"""Chaque primitive validée contre le mock: on vérifie à la fois la SORTIE
(contrat JSON stable) et le PROTOCOLE émis (méthodes/params enregistrés)."""

import json
import pathlib
import stat

import pytest

from cdpx import discovery
from cdpx.client import CDPClient, CDPTimeout
from cdpx.primitives import advanced, audit, capture, dev, inputs, js, nav, net, state
from cdpx.security import RedactionContext

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture()
def client(mock):
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    target = discovery.pick_page("127.0.0.1", mock.http_port, target_id)
    with CDPClient(target["webSocketDebuggerUrl"], timeout=5) as c:
        yield c


# -- nav --------------------------------------------------------------------------


def test_navigate_waits_load(mock, client):
    res = nav.navigate(client, "http://site.test/page", wait="load")
    assert res["ok"] is True and res["frameId"] == "FRAME1"
    assert mock.commands_for("Page.navigate") == [{"url": "http://site.test/page"}]


def test_wait_for_polls_until_found(mock, client):
    mock.on_eval("querySelector", False, False, True)
    res = nav.wait_for(client, "#late-content", timeout=2, poll=0.01)
    assert res["found"] is True
    assert len(mock.commands_for("Runtime.evaluate")) == 3


def test_wait_for_times_out(mock, client):
    mock.on_eval("querySelector", False)
    with pytest.raises(CDPTimeout):
        nav.wait_for(client, "#never", timeout=0.15, poll=0.02)


def test_wait_for_visible_polls_until_element_has_a_non_zero_box(mock, client):
    mock.on_eval("__cdpx_visible", False, False, True)

    res = nav.wait_for_visible(client, "#late-content", timeout=2, poll=0.01)

    assert res["visible"] is True
    assert res["selector"] == "#late-content"
    calls = mock.commands_for("Runtime.evaluate")
    assert len(calls) == 3
    expression = calls[0]["expression"]
    assert ".isConnected" in expression
    assert 'style.display === "none"' in expression
    assert 'style.visibility === "hidden"' in expression
    assert 'style.visibility === "collapse"' in expression
    assert "rect.width > 0 && rect.height > 0" in expression


def test_wait_for_visible_times_out_while_element_stays_hidden(mock, client):
    mock.on_eval("__cdpx_visible", False)

    with pytest.raises(CDPTimeout, match="non visible"):
        nav.wait_for_visible(client, "#hidden", timeout=0.15, poll=0.02)


# -- js ---------------------------------------------------------------------------


def test_evaluate_value_and_exception(mock, client):
    mock.on_eval("1 + 1", 2)
    assert js.evaluate(client, "1 + 1") == 2
    mock.on_eval("boom", {"raw": {"exceptionDetails": {"text": "ReferenceError: boom"}}})
    with pytest.raises(js.JSException, match="boom"):
        js.evaluate(client, "boom()")


def test_get_text_and_html_and_count(mock, client):
    mock.on_eval("innerText", "Bonjour")
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


def test_click_dispatches_mouse_events_at_center(mock, client):
    mock.on_eval("__cdpx_actionability", json.dumps(ACTIONABLE))

    res = inputs.click(client, "#submit-btn")

    assert (res["x"], res["y"]) == (60.0, 35.0)
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
    assert probe["awaitPromise"] is True
    assert probe["returnByValue"] is True
    expression = probe["expression"]
    assert expression.count("requestAnimationFrame") == 2
    assert '.matches(":disabled")' in expression
    assert "aria-disabled" in expression
    assert '.closest("[inert]")' in expression
    assert "pointerEvents" in expression
    assert "document.elementFromPoint" in expression
    assert "element.contains(hit)" in expression


def test_click_element_not_found(mock, client):
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, "attached": False}))

    with pytest.raises(inputs.ElementNotFound, match="sélecteur introuvable"):
        inputs.click(client, "#ghost")
    assert mock.commands_for("Input.dispatchMouseEvent") == []


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({"visible": False}, "non visible"),
        ({"enabled": False}, "désactivé"),
        ({"stable": False}, "instable"),
        ({"receives_events": False}, "recouvert"),
    ],
)
def test_click_refuses_non_actionable_element_without_input(mock, client, state, message):
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, **state}))

    with pytest.raises(inputs.ElementNotInteractable, match=message):
        inputs.click(client, "#blocked")

    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_type_text_clear_selects_then_deletes_through_input_domain(mock, client):
    mock.on_eval("__cdpx_actionability", json.dumps(ACTIONABLE))
    mock.on_eval("__cdpx_prepare_text", True)

    res = inputs.type_text(client, "#name", "Léo", clear=True)

    assert res == {
        "typed": True,
        "value_masked": True,
        "selector": "#name",
        "cleared": True,
    }
    assert "Léo" not in json.dumps(res, ensure_ascii=False)
    evaluations = mock.commands_for("Runtime.evaluate")
    assert len(evaluations) == 2
    prepare = evaluations[1]["expression"]
    assert "el.select()" in prepare
    assert "range.selectNodeContents(el)" in prepare
    assert "selection.removeAllRanges()" in prepare
    assert "el.value =" not in prepare
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
    assert mock.commands_for("Input.insertText") == [{"text": "Léo"}]
    methods = [method for _, method, _ in mock.commands]
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
        ({"visible": False}, "non visible"),
        ({"enabled": False}, "désactivé"),
        ({"editable": False}, "non éditable"),
    ],
)
def test_type_text_refuses_invalid_target_without_input(mock, client, state, message):
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, **state}))

    with pytest.raises(inputs.ElementNotInteractable, match=message):
        inputs.type_text(client, "#blocked", "secret", clear=True)

    assert mock.commands_for("Input.dispatchKeyEvent") == []
    assert mock.commands_for("Input.insertText") == []


def test_type_text_refuses_missing_target_without_input(mock, client):
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, "attached": False}))

    with pytest.raises(inputs.ElementNotFound, match="sélecteur introuvable"):
        inputs.type_text(client, "#missing", "secret", clear=True)

    assert mock.commands_for("Input.dispatchKeyEvent") == []
    assert mock.commands_for("Input.insertText") == []


def test_press_key_enter_sequence(mock, client):
    inputs.press_key(client, "Enter")
    keys = mock.commands_for("Input.dispatchKeyEvent")
    assert [k["type"] for k in keys] == ["rawKeyDown", "char", "keyUp"]
    with pytest.raises(ValueError):
        inputs.press_key(client, "F13")


def test_press_key_backspace_sequence(mock, client):
    result = inputs.press_key(client, "Backspace")

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
    assert inputs.press_key(client, key) == {"pressed": key}
    assert [event["type"] for event in mock.commands_for("Input.dispatchKeyEvent")] == types


# -- capture ----------------------------------------------------------------------


def test_screenshot_writes_valid_png(mock, client, tmp_path):
    out = tmp_path / "shot.png"
    res = capture.screenshot(client, str(out), full_page=True)
    assert out.read_bytes().startswith(b"\x89PNG")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert res["bytes"] > 0
    assert mock.commands_for("Page.captureScreenshot")[0]["captureBeyondViewport"] is True


def test_pdf_writes_valid_signature(mock, client, tmp_path):
    out = tmp_path / "page.pdf"
    capture.pdf(client, str(out))
    assert out.read_bytes().startswith(b"%PDF")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_console_capture_normalizes_entries(mock, client):
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
    assert res["count"] == 2 and res["errors"] == 1
    assert res["entries"][0] == {
        "kind": "console",
        "type": "log",
        "text": "fixture-log 42",
        "ts": 10.0,
    }


def test_console_follow_yields_ndjson_ready_entries(mock, client):
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
    assert entries == [{"kind": "console", "type": "warn", "text": "fixture-warn", "ts": 12.0}]


def test_console_entries_redact_credentials_tokens_and_sensitive_urls():
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

    assert "registered-secret" not in serialized
    assert "bearer-secret" not in serialized
    assert jwt not in serialized
    assert "alice" not in serialized and "password" not in serialized
    assert "https://example.test/callback?code=***" in entries[0]["text"]
    assert context.report.redacted is True


# -- net --------------------------------------------------------------------------


def test_network_capture_assembles_requests(mock, client):
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
    assert res["summary"] == {"total": 3, "failed": 1, "errors_4xx_5xx": 1, "bytes": 123}
    assert res["url"] == "http://s.test/network.html?token=***"
    r1 = next(r for r in res["requests"] if r["requestId"] == "R1")
    assert r1["status"] == 200 and r1["encodedBytes"] == 123
    assert r1["url"] == "http://s.test/api/json?token=***"
    assert mock.commands_for("Page.navigate") == [{"url": navigation_url}]


def test_network_capture_masks_registered_secret_in_url_path(mock, client):
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
    assert secret not in serialized and "reset%2Dtoken%2Dcanary" not in serialized
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


def test_profiler_reads_debug_token_link_and_parses_panels(mock, client, fixtures_http):
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
    assert "token" not in res and res["token_present"] is True
    assert res["profiler_url"].endswith("/_profiler/***")
    assert "fixed-token" not in json.dumps(res)
    assert res["profiler_status"] == 200  # statut réel du fetch du panel
    assert res["panels"]["db"]["queries"] == 6
    assert res["panels"]["db"]["duplicates"] == 4
    assert "signals" not in res and "profiler_bytes" not in res
    assert mock.commands_for("Network.enable") == [{}]
    # protocole émis: un seul fetch page-context, promesse attendue
    (call,) = [
        item
        for item in mock.commands_for("Runtime.evaluate")
        if "__cdpx_profiler_panels" in item["expression"]
    ]
    assert call["awaitPromise"] is True
    assert f'"{link}?panel=db"' in call["expression"]


def test_profiler_prefers_redirect_response_token(mock, client, fixtures_http):
    # Chrome n'émet pas de responseReceived pour une 302: le token de la
    # redirection n'existe que dans requestWillBeSent.redirectResponse et doit
    # gagner sur celui de la page suivie.
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
    assert "token" not in res and res["token_present"] is True
    assert res["profiler_url"].endswith("/_profiler/***")
    assert "redir-token" not in json.dumps(res)
    assert "final-token" not in json.dumps(res)
    assert res["status"] == 302


def test_profiler_falls_back_to_debug_token(mock, client, fixtures_http):
    mock.on_eval("window.location.href", f"{fixtures_http.base_url}/api/profiler-sim")
    mock.script_network(
        _profiler_network_script(fixtures_http.base_url, {"X-Debug-Token": "fixed-token"})
    )
    res = dev.profiler(client, f"{fixtures_http.base_url}/api/profiler-sim", panels=[])
    assert "token" not in res and res["token_present"] is True
    assert res["profiler_url"].endswith("/_profiler/***")
    assert res["response_headers"]["x-debug-token"] == "***"
    assert "fixed-token" not in json.dumps(res)
    # sonde token seule: aucun fetch de panel
    assert res["panels"] == {} and res["profiler_status"] is None
    assert not any(
        "__cdpx_profiler_panels" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_profiler_rejects_requested_origin_before_navigation(mock, client):
    with pytest.raises(ValueError, match="origine refusée"):
        dev.profiler(
            client,
            "https://attacker.example/report",
            panels=["db"],
            allowed_origins=("http://allowed.test",),
        )

    assert mock.commands_for("Network.enable") == []
    assert mock.commands_for("Page.navigate") == []


def test_profiler_rejects_cross_origin_header_before_panel_fetch(mock, client):
    url = "http://allowed.test/report"
    mock.on_eval("window.location.href", url)
    mock.script_network(
        _profiler_network_script(
            "http://allowed.test",
            {"X-Debug-Token-Link": "https://attacker.example/_profiler/stolen"},
        )
    )

    with pytest.raises(ValueError, match="origine refusée"):
        dev.profiler(client, url, panels=["db"])

    assert not any(
        "__cdpx_profiler_panels" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_profiler_rejects_forbidden_final_url_before_panel_fetch(mock, client):
    requested = "http://allowed.test/report"
    mock.on_eval("window.location.href", "https://attacker.example/redirected")
    mock.script_network(
        _profiler_network_script(
            "http://allowed.test",
            {"X-Debug-Token-Link": "http://allowed.test/_profiler/token"},
        )
    )

    with pytest.raises(ValueError, match="origine refusée"):
        dev.profiler(
            client,
            requested,
            panels=["db"],
            allowed_origins=("http://allowed.test",),
        )

    assert not any(
        "__cdpx_profiler_panels" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_dom_diff_runs_action_and_returns_unified_diff(mock, client):
    before = ["<body>", '  <div#result[data-state="idle"]>']
    after = ["<body>", '  <div#result[data-state="submitted"]>', '    "OK:Léo"']
    mock.on_eval("__cdpx_dom_snapshot", json.dumps(before), json.dumps(after))
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    res = dev.dom_diff(client, ["click", "#submit-btn"])
    assert res["changed"] is True
    assert any("submitted" in line for line in res["diff"])
    assert [m["type"] for m in mock.commands_for("Input.dispatchMouseEvent")] == [
        "mouseMoved",
        "mousePressed",
        "mouseReleased",
    ]


# -- state ------------------------------------------------------------------------


def test_cookies_masked_by_default(mock, client):
    res = state.get_cookies(client)
    assert res["values_masked"] is True
    assert res["cookies"][0]["value"] == "***"
    res2 = state.get_cookies(client, show_values=True)
    assert res2["cookies"][0]["value"] == "secret-session-token"


def test_set_and_clear_cookies(mock, client):
    state.set_cookie(client, "flag", "1", "http://127.0.0.1/")
    assert any(c["name"] == "flag" for c in mock.cookies)
    res = state.clear_cookies(client)
    assert res["method"] == "Storage.clearCookies"
    assert mock.cookies == []


def test_clear_cookies_falls_back_on_legacy_method(mock, client):
    # Chrome historique sans Storage.clearCookies: repli sur la méthode dépréciée.
    mock.fail_on("Storage.clearCookies")
    res = state.clear_cookies(client)
    assert res == {"cleared": True, "method": "Network.clearBrowserCookies"}
    assert [m for (_t, m, _p) in mock.commands] == [
        "Storage.clearCookies",
        "Network.clearBrowserCookies",
    ]
    assert mock.cookies == []


def test_get_storage_masks_values_by_default_with_explicit_opt_in(mock, client):
    secret = "storage-secret-value"
    mock.on_eval("localStorage", json.dumps({"cdpx-key": secret}))
    res = state.get_storage(client, "local")
    shown = state.get_storage(client, "local", show_values=True)

    assert res == {
        "kind": "local",
        "entries": {"cdpx-key": "***"},
        "count": 1,
        "values_masked": True,
    }
    assert secret not in json.dumps(res)
    assert shown == {
        "kind": "local",
        "entries": {"cdpx-key": secret},
        "count": 1,
        "values_masked": False,
    }
    assert len(mock.commands_for("Runtime.evaluate")) == 2


# -- audit ------------------------------------------------------------------------

SEO_OK = {
    "url": "http://127.0.0.1/seo.html",
    "lang": "fr",
    "title": "Fixture SEO — page conforme",
    "metas": {"description": "ok", "robots": "index,follow"},
    "canonical": "http://127.0.0.1/seo.html",
    "robots": "index,follow",
    "h1": ["Unique H1 conforme"],
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
    "h1": ["Premier H1", "Deuxième H1 (erreur)"],
    "hreflang": [],
    "jsonld": [],
    "images_without_alt": 2,
    "links": {"internal": 0, "external": 0, "nofollow": 0},
}


def test_seo_clean_page_no_findings(mock, client):
    mock.on_eval("__cdpx_seo", json.dumps(SEO_OK))
    res = audit.seo(client)
    assert res["findings"] == []
    assert res["title_px_estimate"] > 0
    assert res["jsonld"][0]["@type"] == "Product"


def test_seo_broken_page_findings(mock, client):
    mock.on_eval("__cdpx_seo", json.dumps(SEO_BROKEN))
    res = audit.seo(client)
    assert "title manquant" in res["findings"]
    assert "meta description manquante" in res["findings"]
    assert "canonical manquant" in res["findings"]
    assert "2 h1 (attendu: 1)" in res["findings"]
    assert "2 image(s) sans alt" in res["findings"]


def test_seo_advanced_findings(mock, client):
    payload = {
        **SEO_OK,
        "h1": ["Same", "Same"],
        "jsonld": [{"@type": "Product"}, {"__parse_error": "SyntaxError"}],
        "images_without_alt": 1,
    }
    mock.on_eval("__cdpx_seo", json.dumps(payload))
    res = audit.seo(client)
    assert "h1 dupliqué: same" in res["findings"]
    assert "JSON-LD invalide" in res["findings"]
    assert "Product JSON-LD incomplet (sku ou name requis)" in res["findings"]


def test_seo_accepts_top_level_jsonld_arrays_and_reports_scalars(mock, client):
    payload = {
        **SEO_OK,
        "jsonld": [
            [{"@type": "Product", "name": "Valid"}, {"@type": "Product"}],
            "not-an-object",
        ],
    }
    mock.on_eval("__cdpx_seo", json.dumps(payload))
    res = audit.seo(client)
    assert res["findings"] == [
        "Product JSON-LD incomplet (sku ou name requis)",
        "JSON-LD scalaire non supporté",
    ]


def test_metrics(mock, client):
    res = audit.metrics(client)
    assert res["Nodes"] == 42 and res["JSHeapUsedSize"] == 1048576


# -- advanced ------------------------------------------------------------------


def test_intercept_goto_fulfills_matching_request(mock, client):
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
    res = advanced.intercept_goto(client, ["*payment* => 503"], "http://s.test/checkout")
    assert res["hits"] == [{"url": "http://s.test/api/payment", "action": "503"}]
    assert mock.commands_for("Fetch.fulfillRequest")[0]["responseCode"] == 503


def test_intercept_goto_blocks_and_continues(mock, client):
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
    res = advanced.intercept_goto(client, ["*a => block"], "http://s.test/")
    assert res["hits"] == [
        {"url": "http://s.test/a", "action": "block"},
        {"url": "http://s.test/b", "action": "continue"},
    ]
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
    with pytest.raises(ValueError):
        advanced.intercept_goto(client, [rule], "http://s.test/")
    assert mock.commands == []


def test_intercept_prevalidates_every_rule_before_cdp(mock, client):
    with pytest.raises(ValueError):
        advanced.intercept_goto(
            client,
            ["*first* => continue", "*second* => typo"],
            "http://s.test/",
        )
    assert mock.commands == []


@pytest.mark.parametrize("status", [200, 599])
def test_intercept_accepts_status_bounds(mock, client, status):
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
    res = advanced.intercept_goto(
        client,
        [f"*status* => {status}"],
        "http://s.test/",
        settle=0,
    )
    assert res["hits"] == [{"url": "http://s.test/api/status", "action": str(status)}]
    assert mock.commands_for("Fetch.fulfillRequest")[0]["responseCode"] == status


def test_intercept_accepts_explicit_continue(mock, client):
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
    res = advanced.intercept_goto(
        client,
        ["*continue* => continue"],
        "http://s.test/",
        settle=0,
    )
    assert res["hits"] == [{"url": "http://s.test/api/continue", "action": "continue"}]
    assert mock.commands_for("Fetch.continueRequest") == [{"requestId": "I1"}]


def test_emulate_mobile_and_reset(mock, client):
    assert advanced.emulate(client, "mobile")["applied"] is True
    assert mock.commands_for("Emulation.setDeviceMetricsOverride")[0]["mobile"] is True
    mock.commands.clear()
    assert advanced.emulate(client, reset=True)["reset"] is True
    # Séquence complète de reset, UA compris (bug historique: UA mobile jamais
    # restaurée; vérifié contre Chrome réel — setUserAgentOverride "" rétablit
    # l'UA par défaut, clearDeviceMetricsOverride lève l'override device).
    assert [m for (_t, m, _p) in mock.commands] == [
        "Emulation.clearDeviceMetricsOverride",
        "Emulation.setUserAgentOverride",
        "Network.emulateNetworkConditions",
        "Emulation.setCPUThrottlingRate",
    ]
    assert mock.commands_for("Emulation.setUserAgentOverride")[0] == {"userAgent": ""}
    assert mock.commands_for("Emulation.setCPUThrottlingRate")[0] == {"rate": 1}


def test_vitals_installs_observer_and_reads_values(mock, client):
    mock.on_eval("__cdpxVitals", json.dumps({"lcp": 12, "cls": 0.1, "inp": 0}))
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    res = advanced.vitals(client, "http://s.test/vitals.html", click_selector="#inp-button")
    assert res["lcp"] == 12 and res["cls"] == 0.1
    assert mock.commands_for("Page.addScriptToEvaluateOnNewDocument")
    assert mock.commands_for("Input.dispatchMouseEvent")


def test_vitals_rechecks_redirected_origin_before_click(mock, client):
    mock.on_eval("window.location.href", "https://prod.example/redirected")
    with pytest.raises(ValueError, match="mutation refusée"):
        advanced.vitals(
            client,
            "http://allowed.test/vitals.html",
            click_selector="#go",
            origins="http://*.test",
        )
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_a11y_compacts_ax_tree(mock, client):
    res = advanced.a11y(client)
    assert res["count"] == 2
    assert res["nodes"][1]["role"] == "button"


def test_coverage_aggregates_files(mock, client):
    res = advanced.coverage(client, "http://s.test/")
    assert res["files"][0] == {
        "url": "http://fixture/app.js",
        "functions": 1,
        "used_ranges": 0,
        "total_bytes": 0,
        "used_bytes": 0,
        "unused_bytes": 0,
        "coverage_percent": None,
    }
    assert res["js"] == {"total_bytes": 0, "used_bytes": 0, "unused_bytes": 0}
    assert res["css"] == {"rules": 2, "used": 1, "unused": 1}


def test_coverage_reports_byte_coverage_not_range_counts(mock, client, monkeypatch):
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
    res = advanced.coverage(client, "http://s.test/")
    assert res["js"] == {"total_bytes": 100, "used_bytes": 80, "unused_bytes": 20}
    assert res["files"][0]["coverage_percent"] == 80.0


def test_frame_text_reads_iframe_content(mock, client):
    mock.on_eval("contentDocument", "iframe text")
    assert advanced.frame_text(client, "#child-marker")["text"] == "iframe text"


def test_record_executes_action_and_journals_result(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    res = advanced.record(client, str(path), ["click", "#submit"], origins="http://*.test")
    assert res["ok"] is True and res["recorded"] == 1
    # l'action a été réellement exécutée (protocole émis), pas seulement journalisée
    assert [m["type"] for m in mock.commands_for("Input.dispatchMouseEvent")] == [
        "mouseMoved",
        "mousePressed",
        "mouseReleased",
    ]
    event = json.loads(path.read_text().splitlines()[0])
    assert event["action"] == ["click", "#submit"]
    assert event["ok"] is True
    assert event["result"]["clicked"] == "#submit"


def test_record_journals_failure_then_raises(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    mock.on_eval("getBoundingClientRect", None)  # élément introuvable
    with pytest.raises(inputs.ElementNotFound):
        advanced.record(client, str(path), ["click", "#missing"], origins="http://*.test")
    event = json.loads(path.read_text().splitlines()[0])
    assert event["ok"] is False and event["action"] == ["click", "#missing"]
    assert "#missing" in event["result"]["error"]


def test_replay_reexecutes_journal_against_browser(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    advanced.record(client, str(path), ["goto", "http://site.test/"], origins="http://*.test")
    advanced.record(client, str(path), ["click", "#submit"], origins="http://*.test")
    mock.commands.clear()
    res = advanced.replay(client, str(path), origins="http://*.test")
    assert res == {"path": str(path), "events": 2, "played": 2, "ok": True}
    # le rejeu a bien ré-émis navigation puis clic, dans l'ordre du journal
    methods = [m for (_t, m, _p) in mock.commands]
    assert methods.index("Page.navigate") < methods.index("Input.dispatchMouseEvent")


def test_replay_rejects_v1_type_without_exposing_text(client, tmp_path, monkeypatch):
    path = tmp_path / "legacy-type.ndjson"
    path.write_text(
        '{"action":["type","#name","legacy-secret"],"ok":true,'
        '"result":{"typed":"legacy-secret","selector":"#name","cleared":false}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        advanced.actions,
        "run_action",
        lambda _client, _action: {
            "typed": True,
            "value_masked": True,
            "selector": "#name",
            "cleared": False,
        },
    )

    result = advanced.replay(client, str(path), origins="http://*.test")

    assert result["ok"] is False and result["played"] == 0
    assert "v1 sensible" in result["divergence"]
    assert "legacy-secret" not in json.dumps(result)


def test_replay_stops_at_first_divergence(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://site.test/"],"ok":true}\n'
        '{"action":["click","#gone"],"ok":true}\n'
        '{"action":["goto","http://after.test/"],"ok":true}\n',
        encoding="utf-8",
    )
    mock.on_eval("getBoundingClientRect", None)  # le clic rejoué échoue
    res = advanced.replay(client, str(path), origins="http://*.test")
    assert res["ok"] is False and res["played"] == 1
    assert res["divergence"].startswith("event 1:")
    # arrêt net: l'action suivante du journal n'a pas été rejouée
    assert [p.get("url") for p in mock.commands_for("Page.navigate")] == ["http://site.test/"]


def test_replay_divergence_on_journaled_failure(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    path.write_text('{"action":["click","#submit"],"ok":false}\n', encoding="utf-8")
    res = advanced.replay(client, str(path), origins="http://*.test")
    assert res["ok"] is False and res["divergence"] == "event 0: ok=false journalisé"
    assert mock.commands == []  # un enregistrement en échec ne se rejoue pas


def test_replay_validates_journal_before_any_execution(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    path.write_text('{"action":["goto","http://x.test/"],"ok":true}\n{not-json}\n', "utf-8")
    res = advanced.replay(client, str(path), origins="http://*.test")
    assert res["ok"] is False and res["divergence"].startswith("line 2:")
    assert mock.commands == []  # journal corrompu -> rien n'est rejoué
    path.write_text('{"ok":true}\n', encoding="utf-8")
    assert (
        advanced.replay(client, str(path), origins="http://*.test")["divergence"]
        == "line 1: action manquante"
    )
    path.write_text('{"action":["goto","http://x.test/"],"ok":true}\n' * 3, encoding="utf-8")
    with pytest.raises(ValueError):
        advanced.replay(client, str(path), max_actions=2, origins="http://*.test")
    assert mock.commands == []  # budget dépassé -> rien n'est rejoué


def test_replay_validates_action_grammar_before_any_execution(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://x.test/"],"ok":true,"result":{"ok":true}}\n'
        '{"action":["shell","oops"],"ok":true,"result":{}}\n',
        encoding="utf-8",
    )
    res = advanced.replay(client, str(path), origins="http://*.test")
    assert res["ok"] is False and res["divergence"].startswith("line 2:")
    assert mock.commands == []


def test_replay_compares_semantic_results(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://x.test/"],"ok":true,'
        '"result":{"url":"http://other.test/","ok":true,"elapsed_ms":999}}\n',
        encoding="utf-8",
    )
    res = advanced.replay(client, str(path), origins="http://*.test")
    assert res["ok"] is False and res["played"] == 1
    assert res["divergence"] == {
        "event": 0,
        "kind": "result_mismatch",
        "differences": [
            {"path": "$.url", "expected": "http://other.test/", "actual": "http://x.test/"}
        ],
    }


def test_replay_origin_guard_follows_goto_before_mutation(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://prod.example/"],"ok":true}\n'
        '{"action":["click","#submit"],"ok":true}\n',
        encoding="utf-8",
    )
    mock.on_eval("window.location.href", "http://prod.example/")
    res = advanced.replay(client, str(path), origins="http://*.test")
    assert res["ok"] is False and res["played"] == 0
    assert "origine refusée" in str(res["divergence"])
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_replay_origin_guard_uses_redirect_destination_before_mutation(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://allowed.test/start"],"ok":true}\n'
        '{"action":["click","#submit"],"ok":true}\n',
        encoding="utf-8",
    )
    mock.on_eval("window.location.href", "https://prod.example/redirected")

    res = advanced.replay(client, str(path), origins="http://*.test")

    assert res["ok"] is False and res["played"] == 1
    assert "origine refusée" in str(res["divergence"])
    assert mock.commands_for("Input.dispatchMouseEvent") == []
    location_reads = [
        params
        for params in mock.commands_for("Runtime.evaluate")
        if params["expression"] == "window.location.href"
    ]
    assert len(location_reads) == 1  # destination réelle refusée immédiatement après goto


def test_replay_rejects_forbidden_goto_before_navigation(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","https://forbidden.example/"],"ok":true}\n',
        encoding="utf-8",
    )

    result = advanced.replay(
        client,
        str(path),
        origins="http://allowed.test",
    )

    assert result["ok"] is False and result["played"] == 0
    assert "origine refusée" in result["divergence"]
    assert mock.commands_for("Page.navigate") == []


def test_record_rejects_forbidden_goto_before_navigation_or_journal(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"

    with pytest.raises(ValueError, match="origine refusée"):
        advanced.record(
            client,
            str(path),
            ["goto", "https://forbidden.example/"],
            origins="http://allowed.test",
        )

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
    path = tmp_path / "record.ndjson"
    path.write_text(events, encoding="utf-8")
    mock.on_eval("window.location.href", None)

    res = advanced.replay(client, str(path), origins="http://*.test")

    assert res["ok"] is False and res["played"] == played
    assert "URL courante indéterminable" in str(res["divergence"])
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_replay_origin_guard_is_kept_after_mutation(mock, client, tmp_path):
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

    res = advanced.replay(client, str(path), origins="http://*.test")

    assert res["ok"] is False and res["played"] == 1
    assert "destination après action: origine refusée" in str(res["divergence"])
    assert len(mock.commands_for("Input.dispatchMouseEvent")) == 3


def test_origin_guard_blocks_mutations_only_when_configured():
    advanced.assert_origin_allowed("text", "https://prod.example/", "http://*.test")
    with pytest.raises(ValueError):
        advanced.assert_origin_allowed("click", "https://prod.example/", "http://*.test")
    advanced.assert_origin_allowed("click", "http://shop.test/page", "http://*.test")


def test_origin_guard_classifies_commands_by_effective_mutation():
    # Contrat de sécurité: mutations refusées hors CDPX_ORIGINS, lectures permises.
    # Pour les commandes composées, c'est le VERBE de l'action qui décide.
    mutates = advanced.command_mutates
    assert all(mutates(c) for c in ("click", "type", "key", "eval", "intercept"))
    assert mutates("replay")  # le journal rejoué peut contenir n'importe quelle action
    assert not mutates("text") and not mutates("goto") and not mutates("seo")
    for composed in ("dom-diff", "record", "emulate"):
        assert mutates(composed, ["click", "#x"])
        assert mutates(composed, ["eval", "1"])
        assert not mutates(composed, ["goto", "http://x.test/"])
        assert not mutates(composed, [])
    assert not mutates("emulate", None)  # emulate --reset seul: lecture/neutralisation
    assert mutates("vitals", ["click", "#button"])
    assert not mutates("vitals", [])
    assert mutates("cookies", ["set"])
    assert mutates("cookies", ["clear"])
    assert not mutates("cookies", ["get"])


def test_origin_guard_checks_composed_action_verb():
    with pytest.raises(ValueError):
        advanced.assert_origin_allowed(
            "dom-diff", "https://prod.example/", "http://*.test", action=["click", "#x"]
        )
    advanced.assert_origin_allowed(
        "dom-diff", "https://prod.example/", "http://*.test", action=["goto", "http://a.test/"]
    )
    with pytest.raises(ValueError):
        advanced.assert_origin_allowed("replay", "https://prod.example/", "http://*.test")


def test_run_action_dispatches_and_rejects_unknown(mock, client):
    from cdpx.primitives import actions

    res = actions.run_action(client, ["goto", "http://site.test/"])
    assert res["ok"] is True
    assert mock.commands_for("Page.navigate") == [{"url": "http://site.test/"}]
    mock.on_eval("2 + 2", 4)
    assert actions.run_action(client, ["eval", "2 + 2"]) == {"value": 4}
    with pytest.raises(ValueError):
        actions.run_action(client, ["shell", "rm -rf /"])
    with pytest.raises(ValueError):
        actions.run_action(client, [])
