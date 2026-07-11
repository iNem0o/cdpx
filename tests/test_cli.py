"""Le CLI de bout en bout (in-process): parsing args -> découverte -> WS ->
primitive -> JSON sur stdout + exit code. C'est le contrat vu par l'agent."""

import json
import pathlib

import pytest

from cdpx.cli import main


def run(mock, capsys, *argv):
    code = main(["--port", str(mock.http_port), "--timeout", "5", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_tabs_list(mock, capsys):
    code, out, _ = run(mock, capsys, "tabs", "list")
    assert code == 0
    payload = json.loads(out)
    assert payload["count"] == 1
    assert payload["tabs"][0]["type"] == "page" and "id" in payload["tabs"][0]


def test_tabs_list_limit_has_collection_metadata(mock, capsys):
    run(mock, capsys, "tabs", "new", "--url", "http://second.test/")
    code, out, _ = run(mock, capsys, "--limit", "1", "tabs", "list")
    payload = json.loads(out)
    assert code == 0 and payload["count"] == 2
    assert len(payload["tabs"]) == 1
    assert payload["tabs_truncated"] is True
    assert payload["tabs_total"] == 2 and payload["tabs_limit"] == 1


def test_tabs_new_and_close(mock, capsys):
    code, out, _ = run(mock, capsys, "tabs", "new", "--url", "http://x.test/")
    tab = json.loads(out)
    assert code == 0 and tab["url"] == "http://x.test/"
    code, out, _ = run(mock, capsys, "tabs", "close", "--id", tab["id"])
    assert code == 0 and json.loads(out) == {"closed": tab["id"]}


def test_goto(mock, capsys):
    code, out, _ = run(mock, capsys, "goto", "http://site.test/")
    data = json.loads(out)
    assert code == 0 and data["ok"] is True and data["waited"] == "load"


@pytest.mark.scenario(
    feature="browser-navigation",
    journey="open-page",
    scenario_id="browser-navigation.open-page-success",
    proves=["A CDP navigation error is surfaced as runtime exit 1."],
)
def test_goto_error_result_exits_1(mock, capsys, monkeypatch):
    monkeypatch.setattr(
        "cdpx.cli.nav.navigate",
        lambda *a, **kw: {
            "url": "http://bad.test",
            "ok": False,
            "errorText": "ERR_NAME_NOT_RESOLVED",
        },
    )
    code, _, err = run(mock, capsys, "goto", "http://bad.test")
    assert code == 1 and "ERR_NAME_NOT_RESOLVED" in err


def test_eval(mock, capsys):
    mock.on_eval("6 * 7", 42)
    code, out, _ = run(mock, capsys, "eval", "6 * 7")
    assert code == 0 and json.loads(out) == {"value": 42}
    assert out == '{"value":42}\n'


def test_pretty_output_is_explicit(mock, capsys):
    code = main(["--port", str(mock.http_port), "--pretty", "eval", "1"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("{\n")


def test_agent_output_bounds_large_lists(mock, capsys):
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
    assert code == 0
    assert len(data["requests"]) == 2
    assert data["requests_truncated"] is True
    assert data["requests_total"] == 3


def test_console_follow_outputs_compact_ndjson(mock, capsys):
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
    lines = out.splitlines()
    assert code == 0
    assert lines == [
        '{"kind":"console","type":"log","text":"one","ts":1.0}',
        '{"kind":"console","type":"error","text":"two","ts":2.0}',
    ]


def test_seo_with_navigation(mock, capsys):
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
    assert code == 0 and data["findings"] == []
    assert mock.commands_for("Page.navigate") == [{"url": "http://site.test/seo.html"}]


def test_cookies_masked_output(mock, capsys):
    code, out, _ = run(mock, capsys, "cookies", "get")
    data = json.loads(out)
    assert code == 0 and data["cookies"][0]["value"] == "***"


@pytest.mark.parametrize(
    "argv",
    [
        ("tabs", "close"),
        ("tabs", "activate"),
        ("cookies", "set", "--name", "flag"),
        ("tabs", "list", "--id", "x"),
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
    code, _, err = run(mock, capsys, *argv)
    assert code == 2 and ("requis" in err or "non support" in err)
    assert mock.commands == []


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["Mutating command variants cannot bypass the configured origin guard."],
)
def test_cookie_mutations_and_vitals_click_use_origin_guard(mock, capsys, monkeypatch):
    monkeypatch.setenv("CDPX_ORIGINS", "http://*.test")
    for argv in (
        (
            "cookies",
            "set",
            "--name",
            "flag",
            "--value",
            "1",
            "--url",
            "https://prod.example/",
        ),
        ("cookies", "clear"),
        ("vitals", "https://prod.example/", "--click", "#go"),
    ):
        code, _, err = run(mock, capsys, *argv)
        assert code == 1 and "mutation refusée" in err


def test_intercept_checks_destination_origin_not_initial_tab(mock, capsys, monkeypatch):
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
    assert code == 1 and "mutation refusée" in err
    assert mock.commands == []


def test_origin_guard_blocks_cli_mutation(mock, capsys, monkeypatch):
    monkeypatch.setenv("CDPX_ORIGINS", "http://*.test")
    code, _, err = run(mock, capsys, "click", "#submit")
    assert code == 1
    assert "mutation refusée" in err


def test_origin_guard_blocks_dom_diff(mock, capsys, monkeypatch):
    # dom-diff exécute de vraies mutations (click/type/key/eval): même garde que click.
    monkeypatch.setenv("CDPX_ORIGINS", "http://*.test")
    code, _, err = run(mock, capsys, "dom-diff", "--", "click", "#x")
    assert code == 1
    assert "mutation refusée" in err
    assert mock.commands == []  # le guard tire avant la moindre commande CDP


def test_origin_guard_allows_dom_diff_on_allowed_origin(mock, capsys, monkeypatch):
    monkeypatch.setenv("CDPX_ORIGINS", "http://*.test")
    tid = next(iter(mock.targets))
    mock.targets[tid]["url"] = "http://demo.test/page"
    mock.on_eval("__cdpx_dom_snapshot", json.dumps(["<body>"]), json.dumps(["<body>", "  <p>"]))
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    code, out, _ = run(mock, capsys, "dom-diff", "--", "click", "#x")
    assert code == 0 and json.loads(out)["changed"] is True


def test_screenshot(mock, capsys, tmp_path):
    dest = tmp_path / "s.png"
    code, out, _ = run(mock, capsys, "screenshot", "-o", str(dest))
    assert code == 0 and dest.exists()
    assert mock.commands_for("Page.captureScreenshot")[0]["format"] == "png"


def test_screenshot_format_jpeg(mock, capsys, tmp_path):
    dest = tmp_path / "s.jpg"
    code, out, _ = run(mock, capsys, "screenshot", "-o", str(dest), "--format", "jpeg")
    data = json.loads(out)
    assert code == 0 and dest.exists() and data["format"] == "jpeg"
    assert mock.commands_for("Page.captureScreenshot")[0]["format"] == "jpeg"


RECT = json.dumps({"x": 0, "y": 0, "width": 10, "height": 10})

# Filet de dispatch: chaque sous-commande traverse argparse -> _client -> primitive
# -> JSON stdout, et émet au moins sa commande CDP signature (contrat protocole).
# Format: (id, argv, règles on_eval, méthode CDP attendue, prédicat sur la sortie)
DISPATCH_CASES = [
    ("wait", ["wait", "#late"], {"querySelector": True}, "Runtime.evaluate", lambda d: d["found"]),
    (
        "text",
        ["text"],
        {"innerText": "Bonjour"},
        "Runtime.evaluate",
        lambda d: d["text"] == "Bonjour",
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
        ["type", "#name", "Léo"],
        {"focus": True},
        "Input.insertText",
        lambda d: d["typed"] == "Léo",
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
        {"contentDocument": "texte iframe"},
        "Runtime.evaluate",
        lambda d: d["text"] == "texte iframe",
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
def test_cli_dispatch_emits_protocol_and_json(mock, capsys, case_id, argv, rules, method, check):
    for substring, value in rules.items():
        mock.on_eval(substring, value)
    code, out, err = run(mock, capsys, *argv)
    assert code == 0, f"{case_id}: exit {code}, stderr={err}"
    data = json.loads(out)
    assert check(data), f"{case_id}: sortie inattendue {data}"
    if method:
        assert mock.commands_for(method), f"{case_id}: {method} jamais émis"


def test_pdf_cli_writes_valid_signature(mock, capsys, tmp_path):
    dest = tmp_path / "page.pdf"
    code, out, _ = run(mock, capsys, "pdf", "-o", str(dest))
    assert code == 0 and json.loads(out)["bytes"] > 0
    assert dest.read_bytes().startswith(b"%PDF")
    assert mock.commands_for("Page.printToPDF")


def test_tabs_activate(mock, capsys):
    tid = next(iter(mock.targets))
    code, out, _ = run(mock, capsys, "tabs", "activate", "--id", tid)
    assert code == 0 and json.loads(out) == {"activated": tid}


def test_dom_diff_accepts_action_with_or_without_separator(mock, capsys):
    mock.on_eval("__cdpx_dom_snapshot", json.dumps(["<body>"]))
    mock.on_eval("2 + 2", 4)
    code, out, _ = run(mock, capsys, "dom-diff", "eval", "2 + 2")
    assert code == 0 and json.loads(out)["action"] == ["eval", "2 + 2"]
    code, out, _ = run(mock, capsys, "dom-diff", "--", "eval", "2 + 2")
    assert code == 0 and json.loads(out)["action"] == ["eval", "2 + 2"]


def test_profiler_cli_panels_flag(mock, capsys):
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
    code, out, _ = run(
        mock, capsys, "profiler", "http://s.test/", "--settle", "0.05", "--panels", "db"
    )
    data = json.loads(out)
    assert code == 0
    assert data["token"] == "tok"
    assert data["panels"]["db"]["queries"] == 6
    assert "signals" not in data and "profiler_bytes" not in data


def test_profiler_cli_unknown_panel_is_usage_error(mock, capsys):
    with pytest.raises(SystemExit) as exc:
        run(mock, capsys, "profiler", "http://s.test/", "--panels", "doctrine")
    assert exc.value.code == 2
    assert "panel(s) inconnu(s)" in capsys.readouterr().err


def test_intercept_multiple_rules_and_invalid_action(mock, capsys):
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
    assert code == 0 and len(data["rules"]) == 2
    assert mock.commands_for("Fetch.fulfillRequest")[0]["responseCode"] == 503
    # action non-goto: erreur d'usage AVANT toute commande Fetch
    mock.commands.clear()
    code, _, err = run(mock, capsys, "intercept", "--rule", "*x* => block", "--", "click", "#x")
    assert code == 1 and "intercept supporte" in err and mock.commands == []


def test_emulate_requires_preset_or_reset(mock, capsys):
    code, _, err = run(mock, capsys, "emulate")
    assert code == 1 and "preset inconnu" in err


def test_record_cli_executes_and_journals(mock, capsys, tmp_path):
    journal = tmp_path / "j.ndjson"
    code, out, _ = run(mock, capsys, "record", "-o", str(journal), "--", "goto", "http://a.test/")
    data = json.loads(out)
    assert code == 0 and data["ok"] is True and data["recorded"] == 1
    assert mock.commands_for("Page.navigate") == [{"url": "http://a.test/"}]
    event = json.loads(journal.read_text().splitlines()[0])
    assert event["action"] == ["goto", "http://a.test/"]  # le `--` ne fuit pas dans le journal


def test_replay_cli_divergence_exits_1_with_json(mock, capsys, tmp_path):
    journal = tmp_path / "j.ndjson"
    journal.write_text('{"action":["click","#gone"],"ok":true}\n', encoding="utf-8")
    mock.on_eval("getBoundingClientRect", None)
    code, out, _ = run(mock, capsys, "replay", str(journal))
    data = json.loads(out)
    assert code == 1  # divergence = erreur d'exécution, JSON structuré conservé
    assert data["ok"] is False and data["divergence"].startswith("event 0:")


def test_replay_cli_green_journal_exits_0(mock, capsys, tmp_path):
    journal = tmp_path / "j.ndjson"
    journal.write_text('{"action":["goto","http://a.test/"],"ok":true}\n', encoding="utf-8")
    code, out, _ = run(mock, capsys, "replay", str(journal))
    data = json.loads(out)
    assert code == 0 and data == {
        "path": str(journal),
        "events": 1,
        "played": 1,
        "ok": True,
    }


def test_emulate_composed_action_runs_in_same_connection(mock, capsys):
    code, out, _ = run(mock, capsys, "emulate", "mobile", "--", "goto", "http://a.test/")
    data = json.loads(out)
    assert code == 0 and data["applied"] is True
    assert data["action"]["result"]["ok"] is True
    # le preset est posé AVANT l'action, dans la même connexion
    methods = [m for (_t, m, _p) in mock.commands]
    assert methods.index("Emulation.setDeviceMetricsOverride") < methods.index("Page.navigate")


def test_origin_guard_composed_commands_follow_action_verb(mock, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("CDPX_ORIGINS", "http://*.test")
    journal = tmp_path / "j.ndjson"
    # record avec verbe mutant: refusé (aucune commande CDP émise)
    code, _, err = run(mock, capsys, "record", "-o", str(journal), "--", "click", "#x")
    assert code == 1 and "mutation refusée" in err and mock.commands == []
    # replay est gardé séquentiellement: une navigation de lecture vers une
    # origine permise n'est plus refusée à cause de l'onglet initial about:blank.
    journal.write_text('{"action":["goto","http://a.test/"],"ok":true}\n', encoding="utf-8")
    code, out, err = run(mock, capsys, "replay", str(journal))
    assert code == 0 and json.loads(out)["ok"] is True and not err
    # record avec verbe de lecture: permis même hors liste
    code, out, _ = run(mock, capsys, "record", "-o", str(journal), "--", "goto", "http://a.test/")
    assert code == 0 and json.loads(out)["ok"] is True


def test_error_path_exit_code_and_stderr(mock, capsys):
    mock.on_eval("kaboom", {"raw": {"exceptionDetails": {"text": "TypeError: kaboom"}}})
    code, _, err = run(mock, capsys, "eval", "kaboom()")
    assert code == 1 and "kaboom" in err


def test_discovery_error_when_no_chrome(capsys):
    # port 1: fermé à coup sûr -> erreur propre, pas de traceback
    code = main(["--port", "1", "tabs", "list"])
    err = capsys.readouterr().err
    assert code == 1 and "cdpx:" in err


def test_usage_error_exit_2():
    with pytest.raises(SystemExit) as exc:
        main(["goto"])  # url manquante
    assert exc.value.code == 2


def test_cdpx_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("cdpx 0.1.0")
