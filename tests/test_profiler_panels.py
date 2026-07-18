"""Web Profiler panel parsers validated against committed HTML (fixtures
captured from the real Symfony app), plus the page-context fetch via the mock.

The contract: exact counts/classes/routes/statuses, durations never asserted
by value (only by type), and NEVER a parsing exception.
"""

import json
import pathlib

import pytest

from cdpx import discovery
from cdpx.client import CDPClient
from cdpx.orchestration import OrchestrationContext
from cdpx.primitives import profiler
from cdpx.primitives.profiler.html import _menu

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "profiler"
PROFILER_CONTEXT = OrchestrationContext.from_origins("http://app.test")


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
    """The Doctrine panel captured on the real app is reduced to exact
    counts (queries, statements, duplicates) and readable SQL; durations are
    only guaranteed by type."""
    res = profiler.parse_panel("db", 200, read("db.html"))
    #: the real fixture encodes 6 executions for 2 unique statements, hence
    #: 4 duplicates: the parser distinguishes executions from distinct queries
    assert res["available"] is True
    assert res["queries"] == 6
    assert res["statements"] == 2
    assert res["duplicates"] == 4
    #: durations vary from one capture to another: only their type is a contract
    assert isinstance(res["time_ms"], float)
    #: the extracted SQL is the queries' actual text, not a truncated summary
    assert [q["sql"].startswith("SELECT") for q in res["list"]] == [True, True]
    assert "FROM book" in res["list"][0]["sql"]
    assert isinstance(res["list"][0]["duration_ms"], float)


def test_parse_twig_counts_and_templates():
    """The Twig panel is reduced to render counters (templates, blocks,
    macros) and the templates' logical paths, the render duration being
    contractual only by type."""
    res = profiler.parse_panel("twig", 200, read("twig.html"))
    #: the counters really come from the Twig metrics and not from some
    #: other block of the HTML: including zeros, which must stay zeros
    assert res["available"] is True
    assert res["templates"] == 3
    assert res["blocks"] == 0
    assert res["macros"] == 0
    assert isinstance(res["render_ms"], float)
    #: la liste restitue les chemins logiques applicatifs, exploitables pour
    #: spotting an unexpected template
    assert res["list"] == ["scenario/base.html.twig", "scenario/_row.html.twig"]


def test_parse_cache_totals_and_pools():
    """The cache panel delivers the six global totals and the per-pool
    breakdown, faithful to the real capture: this is what allows diagnosing
    an abnormal hit/miss pool by pool."""
    res = profiler.parse_panel("cache", 200, read("cache.html"))
    #: the totals form a coherent set (reads = hits + misses): a column mix-up
    #: in the parser would break this arithmetic
    assert res["available"] is True
    assert (res["calls"], res["reads"], res["hits"]) == (5, 4, 3)
    assert (res["misses"], res["writes"], res["deletes"]) == (1, 1, 0)
    #: the breakdown is addressable by the pool's application name
    assert res["pools"]["app.scenario_pool"]["hits"] == 3
    assert res["pools"]["app.scenario_pool"]["misses"] == 1


def test_parse_exception_absent_then_raised():
    """The exception panel distinguishes 'nothing to report' (structure
    explicitly set to None) from a raised exception whose class and message
    are extracted as-is."""
    res = profiler.parse_panel("exception", 200, read("exception.html"))
    #: the absence of an exception is a complete, closed structure, not an
    #: amputated dict the caller would have to guess
    assert res == {"available": True, "raised": False, "class": None, "message": None}
    raised = profiler.parse_panel("exception", 200, read("exception-raised.html"))
    #: the real exception surfaces its qualified class and exact message,
    #: raw material for the 404 diagnosis
    assert raised["raised"] is True
    assert raised["class"].endswith("NotFoundHttpException")
    assert raised["message"] == "cdpx scenario 404"


def test_parse_exception_global_class_without_namespace():
    """A global exception class (without a namespace) is extracted as-is:
    the parser does not assume an FQCN with backslashes, a case encountered
    on the real routing-500 route."""
    # \RuntimeException: global class, no FQCN — the real routing-500 case.
    html = (
        '<div class="exception-summary"><div class="exception-metadata">'
        '<h2 class="exception-hierarchy"><abbr title="RuntimeException">'
        "RuntimeException</abbr></h2></div>"
        '<div class="exception-message-wrapper">'
        '<h1 class="exception-message">cdpx scenario 500</h1></div></div>'
    )
    res = profiler.parse_panel("exception", 200, html)
    #: the global class is returned without a phantom prefix or truncation:
    #: a parser requiring a backslash would yield class=None here
    assert res["raised"] is True
    assert res["class"] == "RuntimeException"
    assert res["message"] == "cdpx scenario 500"


def test_profiler_free_text_only_redacts_high_confidence_credentials(evidence_case):
    """Redaction of the panels' free text only masks high-confidence
    credentials (Bearer, JWT, URL credentials and query): harmless business
    identifiers survive, otherwise the report becomes unusable."""
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

    exception = profiler.parse_panel("exception", 200, exception_html)
    db = profiler.parse_panel("db", 200, db_html)
    http = profiler.parse_panel("http_client", 200, http_html)
    serialized = json.dumps({"exception": exception, "db": db, "http": http})

    #: none of the injected secret values (Bearer, JWT, URL credentials)
    #: survives the full JSON serialization of the result, regardless of
    #: which panel carried them
    assert "exception-secret" not in serialized
    assert "sql-secret" not in serialized
    assert jwt not in serialized
    assert "alice" not in serialized and "password" not in serialized
    #: harmless business text is not over-redacted: exception message and
    #: SQL stay readable for the diagnosis
    assert "order 123456" in exception["message"]
    assert "order 123456" in db["list"][0]["sql"]
    #: the outgoing URL is finely cleaned: credentials and fragment removed,
    #: sensitive parameter masked, but the endpoint stays identifiable
    assert http["list"][0]["url"] == "https://example.test/api?token=***"

    # Secondary evidence: the serialization of the three panels, where no
    # high-confidence credential survives.
    if evidence_case is not None:
        evidence_case.attach_json(
            "Redacted profiler panels (exception/db/http)",
            {"exception": exception, "db": db, "http": http},
        )


def test_parse_http_client_requests_and_statuses():
    """The HTTP client panel counts requests, clients and errors, and
    describes each outgoing call by method, URL and status: enough to spot
    an unexpected external call."""
    res = profiler.parse_panel("http_client", 200, read("http_client.html"))
    #: the global counters reflect the real capture, zero errors included
    assert res["available"] is True
    assert res["requests"] == 1
    assert res["clients"] == 1
    assert res["errors"] == 0
    #: each outgoing call is identifiable: method, target URL and numeric
    #: status, not a raw string
    assert res["list"][0]["method"] == "GET"
    assert res["list"][0]["url"] == "http://127.0.0.1:8000/api/echo"
    assert res["list"][0]["status"] == 200


def test_parse_messenger_buses_and_classes():
    """The Messenger panel separates dispatched and handled messages, breaks
    them down by bus and names the class of each message: the business flow
    is identifiable without opening the profiler."""
    res = profiler.parse_panel("messenger", 200, read("messenger.html"))
    #: dispatch and handling are counted separately — a queued but
    #: unhandled message would make these two counters diverge
    assert res["available"] is True
    assert res["dispatched"] == 1
    assert res["handled"] == 1
    #: the per-bus breakdown and the FQCN class designate the exact message
    assert res["buses"] == {"messenger.bus.default": 1}
    assert res["list"] == [{"class": "App\\Message\\SyncPing"}]


def test_parse_router_route_controller_status():
    """The routing panel identifies route, controller (FQCN::method), HTTP
    status and absence of redirection from the real request panel."""
    res = profiler.parse_panel("router", 200, read("request.html"))
    #: route and controller are the exact identifiers of the profiled
    #: request: this is what links a measurement to its application code
    assert res["available"] is True
    assert res["route"] == "scenario_profiler"
    assert res["controller"] == "App\\Controller\\ScenarioController::profiler"
    #: numeric status and redirection flag complete the diagnosis
    assert res["status_code"] == 200
    assert res["redirect"] is False


def test_parse_time_metrics_and_timeline():
    """The time panel exposes typed durations (never asserted by value) and
    a timeline where expected events are found by name."""
    res = profiler.parse_panel("time", 200, read("time.html"))
    #: durations are contractual only by type: a pinned value would make
    #: the test dependent on the capture machine
    assert res["available"] is True
    assert isinstance(res["total_ms"], float)
    assert isinstance(res["init_ms"], float)
    names = [e["name"] for e in res["events"]]
    #: the timeline contains the controller and the application stopwatch
    #: section: the parser really reads the events, not just the totals
    assert "controller" in names and "cdpx.section-1" in names


def test_parse_logger_counts():
    """The logs panel reduces to three counters (errors, warnings,
    deprecations) in a closed structure."""
    res = profiler.parse_panel("logger", 200, read("logger.html"))
    #: the strict equality pins the contract: deprecations are counted and
    #: no stray field can sneak into the output
    assert res == {"available": True, "errors": 0, "warnings": 0, "deprecations": 2}


# -- tolerance (never an exception) ---------------------------------------------


def test_panel_unavailable_on_non_200_or_empty():
    """A non-200 status or an empty body translates into a structured
    unavailability carrying the HTTP status, never into an exception or a
    partial parsing of error HTML."""
    #: the original status is kept in the unavailability marker: the caller
    #: knows whether the profiler answered 404 or returned nothing
    assert profiler.parse_panel("db", 404, "<html></html>") == {
        "available": False,
        "status": 404,
    }
    assert profiler.parse_panel("db", 200, "") == {"available": False, "status": 200}


@pytest.mark.parametrize("key", profiler.ALL_PANELS)
def test_parse_garbage_html_never_raises(key):
    """Every panel in the catalog digests malformed HTML without raising:
    the 'never a parsing exception' contract holds for every parser, with
    fields set to zero/None rather than an error."""
    res = profiler.parse_panel(key, 200, "<p>nothing to see <div><span>here</p>")
    #: whatever the panel, broken HTML stays 'available' with no error
    #: field: tolerance is the contract, not a special case
    assert res["available"] is True
    assert "parse_error" not in res  # tolerant: fields at zero/None, not an error


def test_parse_panel_rejects_unknown_key():
    """Parsing tolerance does not extend to call errors: an unknown panel
    key raises immediately instead of returning something empty."""
    #: the programming error is loud, unlike broken HTML, which must be
    #: tolerated
    with pytest.raises(ValueError, match="unknown panel"):
        profiler.parse_panel("nope", 200, "<html></html>")


def test_normalize_panels_defaults_and_rejects():
    """Normalizing the panel selection turns None into 'the whole
    catalog', preserves a valid selection and rejects an unknown key before
    any fetch."""
    #: None expands into the full catalog and a valid selection passes
    #: through intact, in the requested order
    assert profiler.normalize_panels(None) == list(profiler.ALL_PANELS)
    assert profiler.normalize_panels(["db", "twig"]) == ["db", "twig"]
    #: an unknown key fails validation, hence before any request to the
    #: profiler
    with pytest.raises(ValueError, match=r"unknown panel\(s\)"):
        profiler.normalize_panels(["db", "doctrine"])


def test_menu_lists_sidebar_panels():
    """The profiler's sidebar menu is extractable from the HTML of any
    panel: it is what reveals which collectors the real app exposes."""
    menu = _menu(read("db.html"))
    #: the key collectors of the Symfony scenario appear in the menu
    #: extracted from any panel page (here db)
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
                "html": read(f"{profiler.PANEL_SOURCES[key]}.html"),
            }
            for key in keys
        ]
    )


def test_fetch_panels_builds_urls_and_awaits_promise(mock, client):
    """The page-context fetch rebuilds each panel's URL from the profiler
    URL (input querystring discarded) and awaits the Promise with a
    browser-side bounded timeout."""
    mock.on_eval("__cdpx_profiler_panels", _panel_payload("db"))
    fetched = profiler.fetch_panels(
        client, "http://app.test/_profiler/fixed-token?x=1", ["db"], timeout=7
    )
    #: the panel's HTML comes back already parsed, paired with its original status
    assert fetched[0]["panel"] == "db" and fetched[0]["status"] == 200
    (call,) = mock.commands_for("Runtime.evaluate")
    #: the emitted protocol proves the contract: Promise awaited, ?panel=db
    #: URL rebuilt without the input querystring, and timeout translated to
    #: milliseconds in AbortSignal — the fetch cannot hang
    assert call["awaitPromise"] is True
    assert '"http://app.test/_profiler/fixed-token?panel=db"' in call["expression"]
    assert "AbortSignal.timeout(7000)" in call["expression"]


def test_collect_assembles_contract(mock, client, evidence_case):
    """The report assembled by collect is the output contract of `cdpx
    profiler`: parsed panels, masked token and sensitive headers, no secret
    value or internal field in the final JSON."""
    mock.on_eval("__cdpx_profiler_panels", _panel_payload("db", "exception"))
    hit = {
        **HIT,
        "headers": {
            **HIT["headers"],
            "Authorization": "Bearer header-secret",
            "Set-Cookie": "session=header-secret; HttpOnly",
        },
    }
    res = profiler.collect_profiler_report(
        client, hit, context=PROFILER_CONTEXT, panels=["db", "exception"]
    )
    #: the token never appears in clear text: only its presence is
    #: declared, and the profiler URL is masked where it used to appear
    assert "token" not in res and res["token_present"] is True
    assert res["url"] == HIT["url"]
    assert res["profiler_url"] == "http://app.test/_profiler/***"
    assert res["profiler_status"] == 200
    #: every sensitive header is masked individually, and the full JSON
    #: contains none of the secret values injected into the hit
    assert res["response_headers"] == {
        "x-debug-token": "***",
        "Authorization": "***",
        "Set-Cookie": "***",
    }
    assert "fixed-token" not in json.dumps(res)
    assert "header-secret" not in json.dumps(res)
    #: internal collection fields do not leak into the output contract
    assert "signals" not in res and "profiler_bytes" not in res
    #: the requested panels arrive parsed into metrics, not as raw HTML
    assert res["panels"]["db"]["queries"] == 6
    assert res["panels"]["exception"]["raised"] is False

    # Secondary evidence: the masked collect report (token/headers as ***).
    if evidence_case is not None:
        evidence_case.attach_json("Profiler collect report (masked token/headers)", res)


def test_collect_without_panels_probes_token_only(mock, client):
    """With no panel requested, collect limits itself to token detection:
    no JS evaluation is sent to the browser."""
    res = profiler.collect_profiler_report(client, HIT, context=PROFILER_CONTEXT, panels=[])
    #: empty panels and None status on the output side, zero
    #: Runtime.evaluate on the protocol side: the probe costs no browser
    #: round trip
    assert res["panels"] == {} and res["profiler_status"] is None
    assert mock.commands_for("Runtime.evaluate") == []


def test_collect_rejects_unknown_panel_before_fetch(mock, client):
    with pytest.raises(ValueError, match=r"unknown panel\(s\)"):
        profiler.collect_profiler_report(
            client,
            HIT,
            context=PROFILER_CONTEXT,
            panels=["db", "unknown"],
        )

    assert mock.commands_for("Runtime.evaluate") == []


def test_collect_marks_missing_panels_unavailable(mock, client):
    """A panel that was requested but is absent from the fetch response is
    marked unavailable (status 0) instead of disappearing from the report or
    failing the collection."""
    # the fetch only returns db: twig requested -> {"available": false}
    mock.on_eval("__cdpx_profiler_panels", _panel_payload("db"))
    res = profiler.collect_profiler_report(
        client, HIT, context=PROFILER_CONTEXT, panels=["db", "twig"]
    )
    #: the gap is an explicit marker: status 0 distinguishes 'never
    #: fetched' from a real HTTP error response
    assert res["panels"]["twig"] == {"available": False, "status": 0}


def test_collect_resolves_relative_link_before_same_origin_fetch(mock, client):
    """A relative X-Debug-Token-Link is resolved against the page's origin
    before the same-origin fetch: the header's form does not affect the
    collection."""
    hit = {**HIT, "link": "/_profiler/relative-token"}
    mock.on_eval(
        "__cdpx_profiler_panels",
        json.dumps([{"panel": "db", "status": 200, "html": read("db.html")}]),
    )

    result = profiler.collect_profiler_report(client, hit, context=PROFILER_CONTEXT, panels=["db"])

    #: the relative link does not prevent detecting and following the token
    assert result["token_present"] is True
    panel_calls = [
        call
        for call in mock.commands_for("Runtime.evaluate")
        if "__cdpx_profiler_panels" in call["expression"]
    ]
    #: the single fetch targets the absolute URL resolved against the
    #: page's origin: resolution happened before sending it to the browser
    assert len(panel_calls) == 1
    assert '"http://app.test/_profiler/relative-token?panel=db"' in panel_calls[0]["expression"]


def test_collect_rejects_cross_origin_link_before_fetch(mock, client):
    """A profiler link pointing to another origin is refused before any
    fetch: a trapped header cannot redirect the browser toward an attacking
    host."""
    hit = {**HIT, "link": "https://attacker.example/_profiler/stolen"}

    #: le refus est une erreur explicite nommant l'origine, pas un fetch qui
    #: would fail silently
    with pytest.raises(ValueError, match="origin rejected"):
        profiler.collect_profiler_report(client, hit, context=PROFILER_CONTEXT, panels=["db"])

    #: no command went out to the browser: the rejection precedes any
    #: action, that is the security guarantee
    assert mock.commands_for("Runtime.evaluate") == []
