"""Le CLI de bout en bout (in-process): parsing args -> découverte -> WS ->
primitive -> JSON sur stdout + exit code. C'est le contrat vu par l'agent."""

import json

import pytest

from cdpx.cli import main


def run(mock, capsys, *argv):
    code = main(["--port", str(mock.http_port), "--timeout", "5", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_tabs_list(mock, capsys):
    code, out, _ = run(mock, capsys, "tabs", "list")
    assert code == 0
    tabs = json.loads(out)
    assert tabs[0]["type"] == "page" and "id" in tabs[0]


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
