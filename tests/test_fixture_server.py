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
    "/seo-edge.html": ["Produit dupliqué", "{invalid json"],
}


def test_all_pages_served_with_markers(fixtures_http):
    """Chaque page du site témoin existe et porte les points d'ancrage dont
    dépendent les scénarios e2e: une fixture qui bouge casse ici, en clair,
    plutôt que silencieusement dans un test navigateur."""
    for path, markers in PAGES_MARKERS.items():
        status, body, headers = _get(fixtures_http.base_url, path)
        #: la page annoncée au e2e est réellement servie
        assert status == 200, f"{path} -> {status}"
        for marker in markers:
            #: chaque ancre (id, attribut, texte) utilisée par un scénario
            #: e2e est présente dans le HTML servi
            assert marker in body, f"marqueur '{marker}' absent de {path}"
        #: no-store interdit tout cache: le contenu observé par le navigateur
        #: reste déterministe d'une navigation à l'autre
        assert headers.get("Cache-Control") == "no-store"


def test_root_serves_index(fixtures_http):
    """La racine du site témoin sert la page d'accueil elle-même: naviguer
    vers la base URL suffit aux scénarios, sans chemin explicite."""
    status, body, _ = _get(fixtures_http.base_url, "/")
    #: «/» répond avec le marqueur de titre propre à index.html
    assert status == 200 and 'id="main-title"' in body


def test_api_json(fixtures_http):
    """L'endpoint JSON du témoin répond un payload figé au champ près:
    c'est la référence exacte des assertions d'observation réseau."""
    status, body, _ = _get(fixtures_http.base_url, "/api/json")
    #: le corps est intégralement déterministe — toute dérive invaliderait
    #: les comparaisons faites par les scénarios réseau
    assert status == 200
    assert json.loads(body) == {"ok": True, "items": [1, 2, 3], "source": "fixture"}


def test_api_status_codes(fixtures_http):
    """/api/status/<code> rejoue fidèlement le code HTTP demandé, erreurs
    comprises: le témoin sait provoquer des réponses dégradées à la demande."""
    for code in (204, 404, 500):
        status, _, _ = _get(fixtures_http.base_url, f"/api/status/{code}")
        #: le code demandé dans l'URL est restitué tel quel, succès comme
        #: erreur serveur — c'est ce qui rend les pannes scriptables
        assert status == code


def test_api_slow_actually_waits(fixtures_http):
    """/api/slow impose une latence réelle et mesurable, pas seulement
    annoncée: indispensable pour éprouver les timeouts côté navigateur."""
    import time

    t0 = time.monotonic()
    status, body, _ = _get(fixtures_http.base_url, "/api/slow?ms=150")
    #: la réponse déclare la durée dormie demandée dans la requête
    assert status == 200
    assert json.loads(body)["slept_ms"] == 150
    #: l'horloge confirme que l'attente a réellement eu lieu: la latence
    #: n'est pas seulement déclarative
    assert time.monotonic() - t0 >= 0.15


def test_api_echo_post(fixtures_http):
    """/api/echo restitue méthode, chemin et corps d'un POST: le témoin sait
    prouver ce que le navigateur a réellement envoyé sur le fil."""
    req = urllib.request.Request(
        fixtures_http.base_url + "/api/echo", data=b"payload", method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())
    #: l'écho reflète la requête émise dans son intégralité, corps compris,
    #: ce qui permet de vérifier les envois interceptés ou rejoués
    assert data == {"method": "POST", "path": "/api/echo", "body": "payload"}


def test_api_set_cookie(fixtures_http):
    """Le témoin sait poser un cookie via l'en-tête HTTP, matière première
    des scénarios d'état et de masquage des valeurs sensibles."""
    _, _, headers = _get(fixtures_http.base_url, "/api/set-cookie")
    #: le Set-Cookie déterministe attendu par les scénarios d'état est émis
    assert "fixture=on" in headers.get("Set-Cookie", "")


def test_api_profiler_sim(fixtures_http):
    """La simulation du profiler Symfony expose l'en-tête X-Debug-Token-Link,
    point d'entrée que la primitive d'audit suit pour trouver les panels."""
    status, body, headers = _get(fixtures_http.base_url, "/api/profiler-sim")
    #: la réponse s'assume comme simulation et son en-tête pointe vers le
    #: token fixe, exactement comme le ferait une vraie app Symfony en dev
    assert status == 200 and json.loads(body)["profiler"] == "sim"
    assert headers["X-Debug-Token-Link"].endswith("/_profiler/fixed-token")


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
    """Chaque panel simulé du profiler est servi en HTML avec les libellés
    que la primitive d'audit extrait; sans paramètre on retombe sur le panel
    request, et un panel inconnu échoue franchement."""
    for panel, markers in PROFILER_PANEL_MARKERS.items():
        status, body, headers = _get(
            fixtures_http.base_url, f"/_profiler/fixed-token?panel={panel}"
        )
        #: le panel est servi en HTML, comme le vrai profiler Symfony
        assert status == 200, f"panel {panel} -> {status}"
        assert headers["Content-Type"].startswith("text/html"), panel
        for marker in markers:
            #: les libellés que la primitive d'audit repère sont présents
            #: dans le HTML du panel
            assert marker in body, f"marqueur '{marker}' absent du panel {panel}"
    # sans paramètre: panel request; panel inconnu: 404
    status, body, _ = _get(fixtures_http.base_url, "/_profiler/fixed-token")
    #: l'absence de paramètre retombe sur le panel request par défaut,
    #: aligné sur le comportement du profiler réel
    assert status == 200 and "_route" in body
    status, _, _ = _get(fixtures_http.base_url, "/_profiler/fixed-token?panel=nope")
    #: un panel inexistant répond 404 au lieu de servir un HTML vide qui
    #: masquerait une faute de frappe côté audit
    assert status == 404


def test_path_traversal_blocked(fixtures_http):
    """Le serveur témoin ne sert jamais un fichier hors de sa racine: une
    tentative de remontée de chemin est rejetée."""
    status, _, _ = _get(fixtures_http.base_url, "/../pyproject.toml")
    #: le chemin qui remonte vers le dépôt est refusé — aucun fichier du
    #: projet ne peut fuir à travers le serveur de fixtures
    assert status in (403, 404)


def test_unknown_file_404(fixtures_http):
    """Un chemin inconnu répond 404 explicite: pas de repli sur l'index qui
    masquerait la disparition d'une fixture."""
    status, _, _ = _get(fixtures_http.base_url, "/nope.html")
    #: l'absence est signalée franchement, sans page de substitution
    assert status == 404
