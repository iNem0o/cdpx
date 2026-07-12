"""Parseurs des panels du Web Profiler validés sur du HTML committé (fixtures
capturées depuis la vraie app Symfony), plus le fetch page-context via le mock.

Le contrat: comptes/classes/routes/statuts exacts, durées jamais assertées en
valeur (seulement leur type), et JAMAIS d'exception de parsing.
"""

import json
import pathlib

import pytest

from cdpx import discovery
from cdpx.client import CDPClient
from cdpx.primitives import profiler_panels

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "profiler"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def client(mock):
    target_id = next(iter(mock.targets))
    target = discovery.pick_page("127.0.0.1", mock.http_port, target_id)
    with CDPClient(target["webSocketDebuggerUrl"], timeout=5) as c:
        yield c


# -- parseurs par panel -------------------------------------------------------


def test_parse_db_counts_duplicates_and_queries():
    res = profiler_panels.parse_panel("db", 200, read("db.html"))
    assert res["available"] is True
    assert res["queries"] == 6
    assert res["statements"] == 2
    assert res["duplicates"] == 4
    assert isinstance(res["time_ms"], float)
    assert [q["sql"].startswith("SELECT") for q in res["list"]] == [True, True]
    assert "FROM book" in res["list"][0]["sql"]
    assert isinstance(res["list"][0]["duration_ms"], float)


def test_parse_twig_counts_and_templates():
    res = profiler_panels.parse_panel("twig", 200, read("twig.html"))
    assert res["available"] is True
    assert res["templates"] == 3
    assert res["blocks"] == 0
    assert res["macros"] == 0
    assert isinstance(res["render_ms"], float)
    assert res["list"] == ["scenario/base.html.twig", "scenario/_row.html.twig"]


def test_parse_cache_totals_and_pools():
    res = profiler_panels.parse_panel("cache", 200, read("cache.html"))
    assert res["available"] is True
    assert (res["calls"], res["reads"], res["hits"]) == (5, 4, 3)
    assert (res["misses"], res["writes"], res["deletes"]) == (1, 1, 0)
    assert res["pools"]["app.scenario_pool"]["hits"] == 3
    assert res["pools"]["app.scenario_pool"]["misses"] == 1


def test_parse_exception_absent_then_raised():
    res = profiler_panels.parse_panel("exception", 200, read("exception.html"))
    assert res == {"available": True, "raised": False, "class": None, "message": None}
    raised = profiler_panels.parse_panel("exception", 200, read("exception-raised.html"))
    assert raised["raised"] is True
    assert raised["class"].endswith("NotFoundHttpException")
    assert raised["message"] == "cdpx scenario 404"


def test_parse_exception_global_class_without_namespace():
    # \RuntimeException: classe globale, pas de FQCN — le cas routing-500 réel.
    html = (
        '<div class="exception-summary"><div class="exception-metadata">'
        '<h2 class="exception-hierarchy"><abbr title="RuntimeException">'
        "RuntimeException</abbr></h2></div>"
        '<div class="exception-message-wrapper">'
        '<h1 class="exception-message">cdpx scenario 500</h1></div></div>'
    )
    res = profiler_panels.parse_panel("exception", 200, html)
    assert res["raised"] is True
    assert res["class"] == "RuntimeException"
    assert res["message"] == "cdpx scenario 500"


def test_profiler_free_text_only_redacts_high_confidence_credentials():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123"
    exception_html = read("exception-raised.html").replace(
        "cdpx scenario 404",
        (
            "order 123456 Bearer exception-secret "
            f"jwt={jwt} https://alice:password@example.test/error?token=value#fragment"
        ),
    )
    db_html = read("db.html").replace(
        "FROM book t0</pre>",
        (
            "FROM book t0 /* order 123456 Bearer sql-secret "
            "https://alice:password@example.test/query?token=value#fragment */</pre>"
        ),
        1,
    )
    http_html = read("http_client.html").replace(
        "http://127.0.0.1:8000/api/echo",
        "https://alice:password@example.test/api?token=value#fragment",
    )

    exception = profiler_panels.parse_panel("exception", 200, exception_html)
    db = profiler_panels.parse_panel("db", 200, db_html)
    http = profiler_panels.parse_panel("http_client", 200, http_html)
    serialized = json.dumps({"exception": exception, "db": db, "http": http})

    assert "exception-secret" not in serialized
    assert "sql-secret" not in serialized
    assert jwt not in serialized
    assert "alice" not in serialized and "password" not in serialized
    assert "order 123456" in exception["message"]
    assert "order 123456" in db["list"][0]["sql"]
    assert http["list"][0]["url"] == "https://example.test/api?token=***"


def test_parse_http_client_requests_and_statuses():
    res = profiler_panels.parse_panel("http_client", 200, read("http_client.html"))
    assert res["available"] is True
    assert res["requests"] == 1
    assert res["clients"] == 1
    assert res["errors"] == 0
    assert res["list"][0]["method"] == "GET"
    assert res["list"][0]["url"] == "http://127.0.0.1:8000/api/echo"
    assert res["list"][0]["status"] == 200


def test_parse_messenger_buses_and_classes():
    res = profiler_panels.parse_panel("messenger", 200, read("messenger.html"))
    assert res["available"] is True
    assert res["dispatched"] == 1
    assert res["handled"] == 1
    assert res["buses"] == {"messenger.bus.default": 1}
    assert res["list"] == [{"class": "App\\Message\\SyncPing"}]


def test_parse_router_route_controller_status():
    res = profiler_panels.parse_panel("router", 200, read("request.html"))
    assert res["available"] is True
    assert res["route"] == "scenario_profiler"
    assert res["controller"] == "App\\Controller\\ScenarioController::profiler"
    assert res["status_code"] == 200
    assert res["redirect"] is False


def test_parse_time_metrics_and_timeline():
    res = profiler_panels.parse_panel("time", 200, read("time.html"))
    assert res["available"] is True
    assert isinstance(res["total_ms"], float)
    assert isinstance(res["init_ms"], float)
    names = [e["name"] for e in res["events"]]
    assert "controller" in names and "cdpx.section-1" in names


def test_parse_logger_counts():
    res = profiler_panels.parse_panel("logger", 200, read("logger.html"))
    assert res == {"available": True, "errors": 0, "warnings": 0, "deprecations": 2}


# -- tolérance (jamais d'exception) --------------------------------------------


def test_panel_unavailable_on_non_200_or_empty():
    assert profiler_panels.parse_panel("db", 404, "<html></html>") == {
        "available": False,
        "status": 404,
    }
    assert profiler_panels.parse_panel("db", 200, "") == {"available": False, "status": 200}


@pytest.mark.parametrize("key", profiler_panels.ALL_PANELS)
def test_parse_garbage_html_never_raises(key):
    res = profiler_panels.parse_panel(key, 200, "<p>rien à voir <div><span>ici</p>")
    assert res["available"] is True
    assert "parse_error" not in res  # tolérant: champs à zéro/None, pas d'erreur


def test_parse_panel_rejects_unknown_key():
    with pytest.raises(ValueError, match="panel inconnu"):
        profiler_panels.parse_panel("nope", 200, "<html></html>")


def test_normalize_panels_defaults_and_rejects():
    assert profiler_panels.normalize_panels(None) == list(profiler_panels.ALL_PANELS)
    assert profiler_panels.normalize_panels(["db", "twig"]) == ["db", "twig"]
    with pytest.raises(ValueError, match="panel\\(s\\) inconnu"):
        profiler_panels.normalize_panels(["db", "doctrine"])


def test_menu_lists_sidebar_panels():
    menu = profiler_panels._menu(read("db.html"))
    assert {"request", "db", "twig", "cache", "messenger"} <= menu


# -- fetch page-context + assemblage (mock CDP) ---------------------------------


HIT = {
    "url": "http://app.test/scenario/profiler/baseline",
    "status": 200,
    "link": "http://app.test/_profiler/fixed-token",
    "headers": {"x-debug-token": "fixed-token"},
}


def _panel_payload(*keys: str) -> str:
    return json.dumps(
        [
            {
                "panel": key,
                "status": 200,
                "html": read(f"{profiler_panels.PANEL_SOURCES[key]}.html"),
            }
            for key in keys
        ]
    )


def test_fetch_panels_builds_urls_and_awaits_promise(mock, client):
    mock.on_eval("__cdpx_profiler_panels", _panel_payload("db"))
    fetched = profiler_panels.fetch_panels(
        client, "http://app.test/_profiler/fixed-token?x=1", ["db"], timeout=7
    )
    assert fetched[0]["panel"] == "db" and fetched[0]["status"] == 200
    (call,) = mock.commands_for("Runtime.evaluate")
    assert call["awaitPromise"] is True
    assert '"http://app.test/_profiler/fixed-token?panel=db"' in call["expression"]
    assert "AbortSignal.timeout(7000)" in call["expression"]


def test_collect_assembles_contract(mock, client):
    mock.on_eval("__cdpx_profiler_panels", _panel_payload("db", "exception"))
    hit = {
        **HIT,
        "headers": {
            **HIT["headers"],
            "Authorization": "Bearer header-secret",
            "Set-Cookie": "session=header-secret; HttpOnly",
        },
    }
    res = profiler_panels.collect(client, hit, panels=["db", "exception"])
    assert "token" not in res and res["token_present"] is True
    assert res["url"] == HIT["url"]
    assert res["profiler_url"] == "http://app.test/_profiler/***"
    assert res["profiler_status"] == 200
    assert res["response_headers"] == {
        "x-debug-token": "***",
        "Authorization": "***",
        "Set-Cookie": "***",
    }
    assert "fixed-token" not in json.dumps(res)
    assert "header-secret" not in json.dumps(res)
    assert "signals" not in res and "profiler_bytes" not in res
    assert res["panels"]["db"]["queries"] == 6
    assert res["panels"]["exception"]["raised"] is False


def test_collect_without_panels_probes_token_only(mock, client):
    res = profiler_panels.collect(client, HIT, panels=[])
    assert res["panels"] == {} and res["profiler_status"] is None
    assert mock.commands_for("Runtime.evaluate") == []


def test_collect_marks_missing_panels_unavailable(mock, client):
    # le fetch ne renvoie que db: twig demandé -> {"available": false}
    mock.on_eval("__cdpx_profiler_panels", _panel_payload("db"))
    res = profiler_panels.collect(client, HIT, panels=["db", "twig"])
    assert res["panels"]["twig"] == {"available": False, "status": 0}


def test_collect_resolves_relative_link_before_same_origin_fetch(mock, client):
    hit = {**HIT, "link": "/_profiler/relative-token"}
    mock.on_eval(
        "__cdpx_profiler_panels",
        json.dumps([{"panel": "db", "status": 200, "html": read("db.html")}]),
    )

    result = profiler_panels.collect(client, hit, panels=["db"])

    assert result["token_present"] is True
    panel_calls = [
        call
        for call in mock.commands_for("Runtime.evaluate")
        if "__cdpx_profiler_panels" in call["expression"]
    ]
    assert len(panel_calls) == 1
    assert '"http://app.test/_profiler/relative-token?panel=db"' in panel_calls[0]["expression"]


def test_collect_rejects_cross_origin_link_before_fetch(mock, client):
    hit = {**HIT, "link": "https://attacker.example/_profiler/stolen"}

    with pytest.raises(ValueError, match="origine refusée"):
        profiler_panels.collect(client, hit, panels=["db"])

    assert mock.commands_for("Runtime.evaluate") == []
