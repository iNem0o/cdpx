"""The reference site is itself under test: every page and endpoint must
exist and carry the markers expected by e2e (M1). If a fixture moves, it
breaks HERE, not silently in e2e."""

import json
import urllib.error
import urllib.request


def _get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


PAGES_MARKERS = {
    "/index.html": ['id="main-title"', 'href="/form.html"'],
    "/form.html": ['id="submit-btn"', 'id="result"', "data-state"],
    "/spa.html": ["late-content", "300"],
    "/console.html": ["fixture-log", "fixture-error", "fixture-uncaught"],
    "/network.html": ["/api/json", "/api/status/500", "/api/slow"],
    "/seo.html": ["canonical", "ld+json", "hreflang", 'alt="decorative pixel"'],
    "/seo-broken.html": ["First H1", "Second H1"],
    "/storage.html": ["cdpx-key", "jsCookie"],
    "/iframe.html": ['src="/child.html"'],
    "/child.html": ['id="child-marker"'],
    "/long.html": ['id="long-title"', 'id="long-bottom"', "Bottom-of-page marker"],
    "/intercept.html": ['id="intercept-result"', "/api/status/500", "/api/echo"],
    "/interactions-rich.html": [
        'id="hidden-button"',
        'id="disabled-button"',
        'id="aria-disabled-button"',
        'id="inert-button"',
        'id="pointer-events-button"',
        'id="covered-button"',
        'id="descendant-hit-area"',
        'id="controlled-input"',
        "beforeinput",
    ],
    "/vitals.html": ['id="inp-button"', "Largest content candidate"],
    "/coverage.html": ['href="/coverage.css"', 'src="/coverage.js"'],
    "/seo-edge.html": ["Duplicate product", "{invalid json"],
}


def test_all_pages_served_with_markers(fixtures_http, evidence_case):
    """Every page of the reference site exists and carries the anchor
    points that e2e scenarios depend on: a fixture that moves breaks here,
    plainly, rather than silently in a browser test."""
    for path, markers in PAGES_MARKERS.items():
        status, body, headers = _get(fixtures_http.base_url, path)
        #: the page announced to e2e is actually served
        assert status == 200, f"{path} -> {status}"
        for marker in markers:
            #: every anchor (id, attribute, text) used by an e2e scenario
            #: is present in the served HTML
            assert marker in body, f"marker '{marker}' missing from {path}"
        #: no-store forbids any cache: the content observed by the browser
        #: stays deterministic from one navigation to the next
        assert headers.get("Cache-Control") == "no-store"

    if evidence_case is not None:
        # Proof of the fixtures/e2e contract: the page -> verified markers map.
        evidence_case.attach_json("PAGES_MARKERS map (fixtures/e2e contract)", PAGES_MARKERS)


def test_root_serves_index(fixtures_http):
    """The reference site's root serves the home page itself: navigating
    to the base URL is enough for scenarios, with no explicit path."""
    status, body, _ = _get(fixtures_http.base_url, "/")
    #: "/" responds with the title marker specific to index.html
    assert status == 200 and 'id="main-title"' in body


def test_api_json(fixtures_http):
    """The reference server's JSON endpoint responds with a payload fixed
    down to the field: it is the exact reference for network observation
    assertions."""
    status, body, _ = _get(fixtures_http.base_url, "/api/json")
    #: the body is fully deterministic — any drift would invalidate the
    #: comparisons made by network scenarios
    assert status == 200
    assert json.loads(body) == {"ok": True, "items": [1, 2, 3], "source": "fixture"}


def test_api_status_codes(fixtures_http):
    """/api/status/<code> faithfully replays the requested HTTP code,
    errors included: the reference server can trigger degraded responses
    on demand."""
    for code in (204, 404, 500):
        status, _, _ = _get(fixtures_http.base_url, f"/api/status/{code}")
        #: the code requested in the URL is returned as-is, success as well
        #: as server error — this is what makes failures scriptable
        assert status == code


def test_api_slow_actually_waits(fixtures_http):
    """/api/slow imposes a real, measurable latency, not merely a declared
    one: essential for exercising timeouts on the browser side."""
    import time

    t0 = time.monotonic()
    status, body, _ = _get(fixtures_http.base_url, "/api/slow?ms=150")
    #: the response declares the sleep duration requested in the request
    assert status == 200
    assert json.loads(body)["slept_ms"] == 150
    #: the clock confirms the wait actually took place: the latency is not
    #: merely declarative
    assert time.monotonic() - t0 >= 0.15


def test_api_echo_post(fixtures_http):
    """/api/echo returns the method, path, and body of a POST: the
    reference server can prove what the browser actually sent over the wire."""
    req = urllib.request.Request(
        fixtures_http.base_url + "/api/echo", data=b"payload", method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())
    #: the echo reflects the emitted request in full, body included, which
    #: allows verifying intercepted or replayed sends
    assert data == {"method": "POST", "path": "/api/echo", "body": "payload"}


def test_api_set_cookie(fixtures_http):
    """The reference server can set a cookie via the HTTP header, raw
    material for state and sensitive-value redaction scenarios."""
    _, _, headers = _get(fixtures_http.base_url, "/api/set-cookie")
    #: the deterministic Set-Cookie expected by state scenarios is emitted
    assert "fixture=on" in headers.get("Set-Cookie", "")


def test_api_profiler_sim(fixtures_http):
    """The Symfony profiler simulation exposes the X-Debug-Token-Link
    header, the entry point the audit primitive follows to find the panels."""
    status, body, headers = _get(fixtures_http.base_url, "/api/profiler-sim")
    #: the response identifies itself as a simulation and its header points
    #: to the fixed token, exactly as a real Symfony dev app would
    assert status == 200 and json.loads(body)["profiler"] == "sim"
    assert headers["X-Debug-Token-Link"].endswith("/_profiler/fixed-token")


def test_api_profiler_sim_never_reflects_the_request_host(fixtures_http):
    """The simulated profiler link is derived from the loopback server,
    never from an untrusted Host header that could split the response."""
    req = urllib.request.Request(fixtures_http.base_url + "/api/profiler-sim")
    req.add_header("Host", "attacker.example")
    with urllib.request.urlopen(req, timeout=5) as response:
        token_link = response.headers["X-Debug-Token-Link"]

    assert token_link == f"{fixtures_http.base_url}/_profiler/fixed-token"
    assert "attacker.example" not in token_link


PROFILER_PANEL_MARKERS = {
    "db": ["Database Queries", "Different statements"],
    "twig": ["Template Calls", "Rendered Templates"],
    "cache": ["Total hits", "app.scenario_pool"],
    "exception": ["No exception was thrown"],
    "http_client": ["Total requests"],
    "messenger": ["messenger.bus.default"],
    "request": ["_route", "status-response-status-code"],
    "time": ["Total execution time"],
    "logger": ["Deprecations"],
}


def test_profiler_serves_panel_html(fixtures_http):
    """Every simulated profiler panel is served in HTML with the labels
    the audit primitive extracts; with no parameter we fall back to the
    request panel, and an unknown panel fails plainly."""
    for panel, markers in PROFILER_PANEL_MARKERS.items():
        status, body, headers = _get(
            fixtures_http.base_url, f"/_profiler/fixed-token?panel={panel}"
        )
        #: the panel is served in HTML, like the real Symfony profiler
        assert status == 200, f"panel {panel} -> {status}"
        assert headers["Content-Type"].startswith("text/html"), panel
        for marker in markers:
            #: the labels the audit primitive spots are present in the
            #: panel's HTML
            assert marker in body, f"marker '{marker}' missing from panel {panel}"
    # with no parameter: request panel; unknown panel: 404
    status, body, _ = _get(fixtures_http.base_url, "/_profiler/fixed-token")
    #: the absence of a parameter falls back to the request panel by
    #: default, aligned with the real profiler's behavior
    assert status == 200 and "_route" in body
    status, _, _ = _get(fixtures_http.base_url, "/_profiler/fixed-token?panel=nope")
    #: a nonexistent panel responds 404 instead of serving empty HTML that
    #: would mask a typo on the audit side
    assert status == 404


def test_path_traversal_blocked(fixtures_http):
    """The reference server never serves a file outside its root: a path
    traversal attempt is rejected."""
    status, _, _ = _get(fixtures_http.base_url, "/../pyproject.toml")
    #: the path climbing back into the repository is refused — no project
    #: file can leak through the fixture server
    assert status in (403, 404)


def test_unknown_file_404(fixtures_http):
    """An unknown path responds with an explicit 404: no fallback to the
    index that would mask the disappearance of a fixture."""
    status, _, _ = _get(fixtures_http.base_url, "/nope.html")
    #: the absence is reported plainly, with no substitute page
    assert status == 404
