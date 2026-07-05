"""Chaque primitive validée contre le mock: on vérifie à la fois la SORTIE
(contrat JSON stable) et le PROTOCOLE émis (méthodes/params enregistrés)."""

import json

import pytest

from cdpx import discovery
from cdpx.client import CDPClient, CDPTimeout
from cdpx.primitives import advanced, audit, capture, dev, inputs, js, nav, net, state


@pytest.fixture()
def client(mock):
    target = discovery.pick_page("127.0.0.1", mock.http_port)
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


def test_click_dispatches_mouse_events_at_center(mock, client):
    mock.on_eval(
        "getBoundingClientRect", json.dumps({"x": 10, "y": 20, "width": 100, "height": 30})
    )
    res = inputs.click(client, "#submit-btn")
    assert (res["x"], res["y"]) == (60.0, 35.0)
    mouse = mock.commands_for("Input.dispatchMouseEvent")
    assert [m["type"] for m in mouse] == ["mouseMoved", "mousePressed", "mouseReleased"]
    assert all(m["x"] == 60.0 and m["y"] == 35.0 for m in mouse)


def test_click_element_not_found(mock, client):
    mock.on_eval("getBoundingClientRect", None)
    with pytest.raises(inputs.ElementNotFound):
        inputs.click(client, "#ghost")


def test_type_text_focus_then_insert(mock, client):
    mock.on_eval("focus", True)
    res = inputs.type_text(client, "#name", "Léo", clear=True)
    assert res["typed"] == "Léo"
    assert mock.commands_for("Input.insertText") == [{"text": "Léo"}]
    # le clear passe bien dans l'expression de focus
    (expr,) = [p["expression"] for p in mock.commands_for("Runtime.evaluate")]
    assert "el.value = ''" in expr


def test_press_key_enter_sequence(mock, client):
    inputs.press_key(client, "Enter")
    keys = mock.commands_for("Input.dispatchKeyEvent")
    assert [k["type"] for k in keys] == ["rawKeyDown", "char", "keyUp"]
    with pytest.raises(ValueError):
        inputs.press_key(client, "F13")


# -- capture ----------------------------------------------------------------------


def test_screenshot_writes_valid_png(mock, client, tmp_path):
    out = tmp_path / "shot.png"
    res = capture.screenshot(client, str(out), full_page=True)
    assert out.read_bytes().startswith(b"\x89PNG")
    assert res["bytes"] > 0
    assert mock.commands_for("Page.captureScreenshot")[0]["captureBeyondViewport"] is True


def test_pdf_writes_valid_signature(mock, client, tmp_path):
    out = tmp_path / "page.pdf"
    capture.pdf(client, str(out))
    assert out.read_bytes().startswith(b"%PDF")


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


# -- net --------------------------------------------------------------------------


def test_network_capture_assembles_requests(mock, client):
    mock.script_network(
        [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R1",
                    "type": "Fetch",
                    "request": {"url": "http://s.test/api/json", "method": "GET"},
                },
            },
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {"status": 200, "mimeType": "application/json"},
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
    res = net.capture(client, "http://s.test/network.html", settle=0.2)
    assert res["summary"] == {"total": 3, "failed": 1, "errors_4xx_5xx": 1, "bytes": 123}
    r1 = next(r for r in res["requests"] if r["requestId"] == "R1")
    assert r1["status"] == 200 and r1["encodedBytes"] == 123


# -- dev loop ---------------------------------------------------------------------


def test_profiler_reads_debug_token_link(mock, client, fixtures_http):
    link = f"{fixtures_http.base_url}/_profiler/fixed-token"
    mock.script_network(
        [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": f"{fixtures_http.base_url}/api/profiler-sim",
                        "status": 200,
                        "headers": {"X-Debug-Token-Link": link},
                    },
                },
            }
        ]
    )
    res = dev.profiler(client, f"{fixtures_http.base_url}/api/profiler-sim")
    assert res["token"] == "fixed-token"
    assert res["profiler_status"] == 200  # dérivé de la réponse HTTP réelle
    assert res["panels"]["db"]["queries"] == 2
    assert mock.commands_for("Network.enable") == [{}]


def test_profiler_falls_back_to_debug_token(mock, client, fixtures_http):
    mock.script_network(
        [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": f"{fixtures_http.base_url}/api/profiler-sim",
                        "status": 200,
                        "headers": {"X-Debug-Token": "fixed-token"},
                    },
                },
            }
        ]
    )
    res = dev.profiler(client, f"{fixtures_http.base_url}/api/profiler-sim")
    assert res["token"] == "fixed-token"
    assert res["profiler_url"].endswith("/_profiler/fixed-token")


def test_profiler_extracts_cdpx_scenario_signals(mock, client, fixtures_http):
    link = f"{fixtures_http.base_url}/_profiler/fixed-token"
    mock.script_network(
        [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": f"{fixtures_http.base_url}/api/profiler-sim",
                        "status": 200,
                        "headers": {
                            "X-Debug-Token-Link": link,
                            "X-CDPX-Scenario": "profiler.degraded",
                            "X-CDPX-Profiler-Time-Ms": "42",
                            "X-CDPX-Profiler-Memory-Kb": "768",
                            "X-CDPX-Profiler-Db-Queries": "7",
                            "X-CDPX-Profiler-Db-Duplicate-Queries": "2",
                            "X-CDPX-Profiler-Cache-Hit": "0",
                            "X-CDPX-Profiler-Payload-Bytes": "2048",
                        },
                    },
                },
            }
        ]
    )

    res = dev.profiler(client, f"{fixtures_http.base_url}/api/profiler-sim")

    assert res["signals"] == {
        "scenario": "profiler.degraded",
        "time_ms": 42,
        "memory_kb": 768,
        "db_queries": 7,
        "db_duplicate_queries": 2,
        "cache_hit": False,
        "payload_bytes": 2048,
    }


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


def test_get_storage(mock, client):
    mock.on_eval("localStorage", json.dumps({"cdpx-key": "cdpx-value"}))
    res = state.get_storage(client, "local")
    assert res["entries"] == {"cdpx-key": "cdpx-value"} and res["count"] == 1


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


def test_intercept_rejects_invalid_rule(mock, client):
    with pytest.raises(ValueError):
        advanced.intercept_goto(client, ["broken"], "http://s.test/")


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


def test_a11y_compacts_ax_tree(mock, client):
    res = advanced.a11y(client)
    assert res["count"] == 2
    assert res["nodes"][1]["role"] == "button"


def test_coverage_aggregates_files(mock, client):
    res = advanced.coverage(client, "http://s.test/")
    assert res["files"] == [{"url": "http://fixture/app.js", "functions": 1, "used_ranges": 0}]
    assert res["css"] == {"rules": 2, "used": 1, "unused": 1}


def test_frame_text_reads_iframe_content(mock, client):
    mock.on_eval("contentDocument", "iframe text")
    assert advanced.frame_text(client, "#child-marker")["text"] == "iframe text"


def test_record_executes_action_and_journals_result(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    res = advanced.record(client, str(path), ["click", "#submit"])
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
        advanced.record(client, str(path), ["click", "#missing"])
    event = json.loads(path.read_text().splitlines()[0])
    assert event["ok"] is False and event["action"] == ["click", "#missing"]
    assert "#missing" in event["result"]["error"]


def test_replay_reexecutes_journal_against_browser(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    advanced.record(client, str(path), ["goto", "http://site.test/"])
    advanced.record(client, str(path), ["click", "#submit"])
    mock.commands.clear()
    res = advanced.replay(client, str(path))
    assert res == {"path": str(path), "events": 2, "played": 2, "ok": True}
    # le rejeu a bien ré-émis navigation puis clic, dans l'ordre du journal
    methods = [m for (_t, m, _p) in mock.commands]
    assert methods.index("Page.navigate") < methods.index("Input.dispatchMouseEvent")


def test_replay_stops_at_first_divergence(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://site.test/"],"ok":true}\n'
        '{"action":["click","#gone"],"ok":true}\n'
        '{"action":["goto","http://after.test/"],"ok":true}\n',
        encoding="utf-8",
    )
    mock.on_eval("getBoundingClientRect", None)  # le clic rejoué échoue
    res = advanced.replay(client, str(path))
    assert res["ok"] is False and res["played"] == 1
    assert res["divergence"].startswith("event 1:")
    # arrêt net: l'action suivante du journal n'a pas été rejouée
    assert [p.get("url") for p in mock.commands_for("Page.navigate")] == ["http://site.test/"]


def test_replay_divergence_on_journaled_failure(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    path.write_text('{"action":["click","#submit"],"ok":false}\n', encoding="utf-8")
    res = advanced.replay(client, str(path))
    assert res["ok"] is False and res["divergence"] == "event 0: ok=false journalisé"
    assert mock.commands == []  # un enregistrement en échec ne se rejoue pas


def test_replay_validates_journal_before_any_execution(mock, client, tmp_path):
    path = tmp_path / "record.ndjson"
    path.write_text('{"action":["goto","http://x.test/"],"ok":true}\n{not-json}\n', "utf-8")
    res = advanced.replay(client, str(path))
    assert res["ok"] is False and res["divergence"].startswith("line 2:")
    assert mock.commands == []  # journal corrompu -> rien n'est rejoué
    path.write_text('{"ok":true}\n', encoding="utf-8")
    assert advanced.replay(client, str(path))["divergence"] == "line 1: action manquante"
    path.write_text('{"action":["goto","http://x.test/"],"ok":true}\n' * 3, encoding="utf-8")
    with pytest.raises(ValueError):
        advanced.replay(client, str(path), max_actions=2)
    assert mock.commands == []  # budget dépassé -> rien n'est rejoué


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
