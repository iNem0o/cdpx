"""Le site témoin est lui-même sous test: chaque page et endpoint doit exister
et porter les marqueurs attendus par le e2e (M1). Si une fixture bouge, ça
casse ICI, pas silencieusement dans le e2e."""

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
    "/seo.html": ["canonical", "ld+json", "hreflang", 'alt="pixel décoratif"'],
    "/seo-broken.html": ["Premier H1", "Deuxième H1"],
    "/storage.html": ["cdpx-key", "jsCookie"],
    "/iframe.html": ['src="/child.html"'],
    "/child.html": ['id="child-marker"'],
    "/long.html": ['id="long-title"', 'id="long-bottom"', "Marqueur bas de page"],
    "/intercept.html": ['id="intercept-result"', "/api/status/500", "/api/echo"],
    "/vitals.html": ['id="inp-button"', "Largest content candidate"],
    "/coverage.html": ['href="/coverage.css"', 'src="/coverage.js"'],
    "/seo-edge.html": ["Produit dupliqué", "{invalid json"],
}


def test_all_pages_served_with_markers(fixtures_http):
    for path, markers in PAGES_MARKERS.items():
        status, body, headers = _get(fixtures_http.base_url, path)
        assert status == 200, f"{path} -> {status}"
        for marker in markers:
            assert marker in body, f"marqueur '{marker}' absent de {path}"
        assert headers.get("Cache-Control") == "no-store"


def test_root_serves_index(fixtures_http):
    status, body, _ = _get(fixtures_http.base_url, "/")
    assert status == 200 and 'id="main-title"' in body


def test_api_json(fixtures_http):
    status, body, _ = _get(fixtures_http.base_url, "/api/json")
    assert status == 200
    assert json.loads(body) == {"ok": True, "items": [1, 2, 3], "source": "fixture"}


def test_api_status_codes(fixtures_http):
    for code in (204, 404, 500):
        status, _, _ = _get(fixtures_http.base_url, f"/api/status/{code}")
        assert status == code


def test_api_slow_actually_waits(fixtures_http):
    import time

    t0 = time.monotonic()
    status, body, _ = _get(fixtures_http.base_url, "/api/slow?ms=150")
    assert status == 200
    assert json.loads(body)["slept_ms"] == 150
    assert time.monotonic() - t0 >= 0.15


def test_api_echo_post(fixtures_http):
    req = urllib.request.Request(
        fixtures_http.base_url + "/api/echo", data=b"payload", method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())
    assert data == {"method": "POST", "path": "/api/echo", "body": "payload"}


def test_api_set_cookie(fixtures_http):
    _, _, headers = _get(fixtures_http.base_url, "/api/set-cookie")
    assert "fixture=on" in headers.get("Set-Cookie", "")


def test_api_profiler_sim(fixtures_http):
    status, body, headers = _get(fixtures_http.base_url, "/api/profiler-sim")
    assert status == 200 and json.loads(body)["profiler"] == "sim"
    assert headers["X-Debug-Token-Link"].endswith("/_profiler/fixed-token")
    status, body, _ = _get(fixtures_http.base_url, "/_profiler/fixed-token")
    assert status == 200
    assert json.loads(body)["db"]["queries"] == 2


def test_path_traversal_blocked(fixtures_http):
    status, _, _ = _get(fixtures_http.base_url, "/../pyproject.toml")
    assert status in (403, 404)


def test_unknown_file_404(fixtures_http):
    status, _, _ = _get(fixtures_http.base_url, "/nope.html")
    assert status == 404
