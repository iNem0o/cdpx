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
    """Le panel Doctrine capturé sur la vraie app est réduit à des comptes
    exacts (requêtes, statements, doublons) et à du SQL lisible; les durées ne
    sont garanties qu'en type."""
    res = profiler.parse_panel("db", 200, read("db.html"))
    #: la fixture réelle encode 6 exécutions pour 2 statements uniques, donc
    #: 4 doublons: le parseur distingue exécutions et requêtes distinctes
    assert res["available"] is True
    assert res["queries"] == 6
    assert res["statements"] == 2
    assert res["duplicates"] == 4
    #: les durées varient d'une capture à l'autre: seul leur type est un contrat
    assert isinstance(res["time_ms"], float)
    #: le SQL extrait est le vrai texte des requêtes, pas un résumé tronqué
    assert [q["sql"].startswith("SELECT") for q in res["list"]] == [True, True]
    assert "FROM book" in res["list"][0]["sql"]
    assert isinstance(res["list"][0]["duration_ms"], float)


def test_parse_twig_counts_and_templates():
    """Le panel Twig est réduit aux compteurs de rendu (templates, blocks,
    macros) et aux chemins logiques des templates, la durée de rendu n'étant
    contractuelle qu'en type."""
    res = profiler.parse_panel("twig", 200, read("twig.html"))
    #: les compteurs proviennent bien des métriques Twig et pas d'un autre
    #: bloc du HTML: y compris les zéros, qui doivent rester des zéros
    assert res["available"] is True
    assert res["templates"] == 3
    assert res["blocks"] == 0
    assert res["macros"] == 0
    assert isinstance(res["render_ms"], float)
    #: la liste restitue les chemins logiques applicatifs, exploitables pour
    #: repérer un template inattendu
    assert res["list"] == ["scenario/base.html.twig", "scenario/_row.html.twig"]


def test_parse_cache_totals_and_pools():
    """Le panel cache livre les six totaux globaux et la ventilation par pool,
    fidèles à la capture réelle: c'est ce qui permet de diagnostiquer un
    hit/miss anormal pool par pool."""
    res = profiler.parse_panel("cache", 200, read("cache.html"))
    #: les totaux forment un ensemble cohérent (reads = hits + misses):
    #: une confusion de colonnes dans le parseur casserait cette arithmétique
    assert res["available"] is True
    assert (res["calls"], res["reads"], res["hits"]) == (5, 4, 3)
    assert (res["misses"], res["writes"], res["deletes"]) == (1, 1, 0)
    #: la ventilation est adressable par le nom applicatif du pool
    assert res["pools"]["app.scenario_pool"]["hits"] == 3
    assert res["pools"]["app.scenario_pool"]["misses"] == 1


def test_parse_exception_absent_then_raised():
    """Le panel exception distingue 'rien à signaler' (structure explicite à
    None) d'une exception levée dont classe et message sont extraits tels
    quels."""
    res = profiler.parse_panel("exception", 200, read("exception.html"))
    #: l'absence d'exception est une structure complète et fermée, pas un
    #: dict amputé que l'appelant devrait deviner
    assert res == {"available": True, "raised": False, "class": None, "message": None}
    raised = profiler.parse_panel("exception", 200, read("exception-raised.html"))
    #: l'exception réelle remonte sa classe qualifiée et son message exact,
    #: matière première du diagnostic 404
    assert raised["raised"] is True
    assert raised["class"].endswith("NotFoundHttpException")
    assert raised["message"] == "cdpx scenario 404"


def test_parse_exception_global_class_without_namespace():
    """Une classe d'exception globale (sans namespace) est extraite telle
    quelle: le parseur ne présuppose pas un FQCN avec antislashs, cas
    rencontré sur la vraie route routing-500."""
    # \RuntimeException: classe globale, pas de FQCN — le cas routing-500 réel.
    html = (
        '<div class="exception-summary"><div class="exception-metadata">'
        '<h2 class="exception-hierarchy"><abbr title="RuntimeException">'
        "RuntimeException</abbr></h2></div>"
        '<div class="exception-message-wrapper">'
        '<h1 class="exception-message">cdpx scenario 500</h1></div></div>'
    )
    res = profiler.parse_panel("exception", 200, html)
    #: la classe globale est restituée sans préfixe fantôme ni troncature:
    #: un parseur qui exigerait un antislash rendrait class=None ici
    assert res["raised"] is True
    assert res["class"] == "RuntimeException"
    assert res["message"] == "cdpx scenario 500"


def test_profiler_free_text_only_redacts_high_confidence_credentials(evidence_case):
    """La redaction du texte libre des panels ne masque que les credentials à
    haute confiance (Bearer, JWT, credentials et query d'URL): les identifiants
    métier anodins survivent, sinon le rapport devient inexploitable."""
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

    #: aucune des valeurs secrètes injectées (Bearer, JWT, credentials d'URL)
    #: ne survit à la sérialisation JSON complète du résultat, quel que soit
    #: le panel qui les transportait
    assert "exception-secret" not in serialized
    assert "sql-secret" not in serialized
    assert jwt not in serialized
    assert "alice" not in serialized and "password" not in serialized
    #: le texte métier anodin n'est pas sur-redacté: message d'exception et
    #: SQL restent lisibles pour le diagnostic
    assert "order 123456" in exception["message"]
    assert "order 123456" in db["list"][0]["sql"]
    #: l'URL sortante est nettoyée finement: credentials et fragment retirés,
    #: paramètre sensible masqué, mais l'endpoint reste identifiable
    assert http["list"][0]["url"] == "https://example.test/api?token=***"

    # Preuve secondaire: la sérialisation des trois panels, où aucun credential
    # à haute confiance ne survit.
    if evidence_case is not None:
        evidence_case.attach_json(
            "Panels profiler redactés (exception/db/http)",
            {"exception": exception, "db": db, "http": http},
        )


def test_parse_http_client_requests_and_statuses():
    """Le panel HTTP client compte requêtes, clients et erreurs, et décrit
    chaque appel sortant par méthode, URL et statut: de quoi repérer un appel
    externe imprévu."""
    res = profiler.parse_panel("http_client", 200, read("http_client.html"))
    #: les compteurs globaux reflètent la capture réelle, zéro erreur compris
    assert res["available"] is True
    assert res["requests"] == 1
    assert res["clients"] == 1
    assert res["errors"] == 0
    #: chaque appel sortant est identifiable: méthode, URL cible et statut
    #: numérique, pas une chaîne brute
    assert res["list"][0]["method"] == "GET"
    assert res["list"][0]["url"] == "http://127.0.0.1:8000/api/echo"
    assert res["list"][0]["status"] == 200


def test_parse_messenger_buses_and_classes():
    """Le panel Messenger sépare messages dispatchés et traités, les ventile
    par bus et nomme la classe de chaque message: le flux métier est
    identifiable sans ouvrir le profiler."""
    res = profiler.parse_panel("messenger", 200, read("messenger.html"))
    #: dispatch et traitement sont comptés séparément — un message queué non
    #: traité ferait diverger ces deux compteurs
    assert res["available"] is True
    assert res["dispatched"] == 1
    assert res["handled"] == 1
    #: la ventilation par bus et la classe FQCN désignent le message précis
    assert res["buses"] == {"messenger.bus.default": 1}
    assert res["list"] == [{"class": "App\\Message\\SyncPing"}]


def test_parse_router_route_controller_status():
    """Le panel routing identifie route, contrôleur (FQCN::méthode), statut
    HTTP et absence de redirection à partir du panel request réel."""
    res = profiler.parse_panel("router", 200, read("request.html"))
    #: route et contrôleur sont les identifiants exacts de la requête
    #: profilée: c'est ce qui relie une mesure à son code applicatif
    assert res["available"] is True
    assert res["route"] == "scenario_profiler"
    assert res["controller"] == "App\\Controller\\ScenarioController::profiler"
    #: statut numérique et drapeau de redirection complètent le diagnostic
    assert res["status_code"] == 200
    assert res["redirect"] is False


def test_parse_time_metrics_and_timeline():
    """Le panel temps expose des durées typées (jamais assertées en valeur)
    et une timeline où les évènements attendus sont retrouvés par nom."""
    res = profiler.parse_panel("time", 200, read("time.html"))
    #: les durées ne sont contractuelles qu'en type: une valeur figée
    #: rendrait le test dépendant de la machine de capture
    assert res["available"] is True
    assert isinstance(res["total_ms"], float)
    assert isinstance(res["init_ms"], float)
    names = [e["name"] for e in res["events"]]
    #: la timeline contient le contrôleur et la section stopwatch applicative:
    #: le parseur lit bien les évènements, pas seulement les totaux
    assert "controller" in names and "cdpx.section-1" in names


def test_parse_logger_counts():
    """Le panel logs se réduit à trois compteurs (erreurs, warnings,
    dépréciations) dans une structure fermée."""
    res = profiler.parse_panel("logger", 200, read("logger.html"))
    #: l'égalité stricte fige le contrat: les dépréciations sont comptées et
    #: aucun champ parasite ne peut s'inviter dans la sortie
    assert res == {"available": True, "errors": 0, "warnings": 0, "deprecations": 2}


# -- tolérance (jamais d'exception) --------------------------------------------


def test_panel_unavailable_on_non_200_or_empty():
    """Un statut non-200 ou un corps vide se traduit en indisponibilité
    structurée portant le statut HTTP, jamais en exception ni en parsing
    partiel de HTML d'erreur."""
    #: le statut d'origine est conservé dans le marqueur d'indisponibilité:
    #: l'appelant sait si le profiler a répondu 404 ou renvoyé du vide
    assert profiler.parse_panel("db", 404, "<html></html>") == {
        "available": False,
        "status": 404,
    }
    assert profiler.parse_panel("db", 200, "") == {"available": False, "status": 200}


@pytest.mark.parametrize("key", profiler.ALL_PANELS)
def test_parse_garbage_html_never_raises(key):
    """Chaque panel du catalogue digère du HTML malformé sans lever: le
    contrat 'jamais d'exception de parsing' tient pour tous les parseurs,
    avec des champs à zéro/None plutôt qu'une erreur."""
    res = profiler.parse_panel(key, 200, "<p>rien à voir <div><span>ici</p>")
    #: quel que soit le panel, un HTML cassé reste 'available' sans champ
    #: d'erreur: la tolérance est le contrat, pas un cas particulier
    assert res["available"] is True
    assert "parse_error" not in res  # tolérant: champs à zéro/None, pas d'erreur


def test_parse_panel_rejects_unknown_key():
    """La tolérance de parsing ne s'étend pas aux erreurs d'appel: une clé de
    panel inconnue lève immédiatement au lieu de retourner du vide."""
    #: l'erreur de programmation est bruyante, contrairement au HTML cassé
    #: qui, lui, doit être toléré
    with pytest.raises(ValueError, match="unknown panel"):
        profiler.parse_panel("nope", 200, "<html></html>")


def test_normalize_panels_defaults_and_rejects():
    """La normalisation de la sélection de panels fait de None 'tout le
    catalogue', préserve une sélection valide et rejette une clé inconnue
    avant tout fetch."""
    #: None se déploie en catalogue complet et une sélection valide traverse
    #: intacte, dans l'ordre demandé
    assert profiler.normalize_panels(None) == list(profiler.ALL_PANELS)
    assert profiler.normalize_panels(["db", "twig"]) == ["db", "twig"]
    #: une clé inconnue échoue à la validation, donc avant toute requête
    #: vers le profiler
    with pytest.raises(ValueError, match=r"unknown panel\(s\)"):
        profiler.normalize_panels(["db", "doctrine"])


def test_menu_lists_sidebar_panels():
    """Le menu latéral du profiler est extractible du HTML de n'importe quel
    panel: c'est lui qui révèle quels collecteurs la vraie app expose."""
    menu = _menu(read("db.html"))
    #: les collecteurs clés du scénario Symfony figurent dans le menu extrait
    #: d'une page panel quelconque (ici db)
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
    """Le fetch page-context reconstruit l'URL de chaque panel depuis l'URL
    profiler (querystring d'entrée écartée) et attend la Promise avec un
    timeout borné côté navigateur."""
    mock.on_eval("__cdpx_profiler_panels", _panel_payload("db"))
    fetched = profiler.fetch_panels(
        client, "http://app.test/_profiler/fixed-token?x=1", ["db"], timeout=7
    )
    #: le HTML du panel revient déjà parsé, associé à son statut d'origine
    assert fetched[0]["panel"] == "db" and fetched[0]["status"] == 200
    (call,) = mock.commands_for("Runtime.evaluate")
    #: le protocole émis prouve le contrat: Promise attendue, URL ?panel=db
    #: reconstruite sans la querystring d'entrée, et timeout traduit en
    #: millisecondes dans AbortSignal — le fetch ne peut pas pendre
    assert call["awaitPromise"] is True
    assert '"http://app.test/_profiler/fixed-token?panel=db"' in call["expression"]
    assert "AbortSignal.timeout(7000)" in call["expression"]


def test_collect_assembles_contract(mock, client, evidence_case):
    """Le rapport assemblé par collect est le contrat de sortie de `cdpx
    profiler`: panels parsés, token et headers sensibles masqués, aucune
    valeur secrète ni champ interne dans le JSON final."""
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
    #: le token n'apparaît jamais en clair: seule sa présence est déclarée,
    #: et l'URL profiler est masquée à l'endroit où il figurait
    assert "token" not in res and res["token_present"] is True
    assert res["url"] == HIT["url"]
    assert res["profiler_url"] == "http://app.test/_profiler/***"
    assert res["profiler_status"] == 200
    #: chaque header sensible est masqué individuellement, et le JSON complet
    #: ne contient aucune des valeurs secrètes injectées dans le hit
    assert res["response_headers"] == {
        "x-debug-token": "***",
        "Authorization": "***",
        "Set-Cookie": "***",
    }
    assert "fixed-token" not in json.dumps(res)
    assert "header-secret" not in json.dumps(res)
    #: les champs internes de collecte ne fuient pas dans le contrat de sortie
    assert "signals" not in res and "profiler_bytes" not in res
    #: les panels demandés arrivent parsés en métriques, pas en HTML brut
    assert res["panels"]["db"]["queries"] == 6
    assert res["panels"]["exception"]["raised"] is False

    # Preuve secondaire: le rapport collect masqué (token/headers à ***).
    if evidence_case is not None:
        evidence_case.attach_json("Rapport collect profiler (token/headers masqués)", res)


def test_collect_without_panels_probes_token_only(mock, client):
    """Sans panel demandé, collect se limite à la détection du token: aucune
    évaluation JS n'est envoyée au navigateur."""
    res = profiler.collect_profiler_report(client, HIT, context=PROFILER_CONTEXT, panels=[])
    #: panels vides et statut None côté sortie, zéro Runtime.evaluate côté
    #: protocole: la sonde ne coûte aucun aller-retour navigateur
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
    """Un panel demandé mais absent de la réponse du fetch est marqué
    indisponible (statut 0) au lieu de disparaître du rapport ou de faire
    échouer la collecte."""
    # le fetch ne renvoie que db: twig demandé -> {"available": false}
    mock.on_eval("__cdpx_profiler_panels", _panel_payload("db"))
    res = profiler.collect_profiler_report(
        client, HIT, context=PROFILER_CONTEXT, panels=["db", "twig"]
    )
    #: le manque est un marqueur explicite: statut 0 distingue 'jamais
    #: récupéré' d'une vraie réponse HTTP en erreur
    assert res["panels"]["twig"] == {"available": False, "status": 0}


def test_collect_resolves_relative_link_before_same_origin_fetch(mock, client):
    """Un X-Debug-Token-Link relatif est résolu contre l'origine de la page
    avant le fetch same-origin: la forme du header n'affecte pas la
    collecte."""
    hit = {**HIT, "link": "/_profiler/relative-token"}
    mock.on_eval(
        "__cdpx_profiler_panels",
        json.dumps([{"panel": "db", "status": 200, "html": read("db.html")}]),
    )

    result = profiler.collect_profiler_report(client, hit, context=PROFILER_CONTEXT, panels=["db"])

    #: le lien relatif n'empêche pas de détecter et suivre le token
    assert result["token_present"] is True
    panel_calls = [
        call
        for call in mock.commands_for("Runtime.evaluate")
        if "__cdpx_profiler_panels" in call["expression"]
    ]
    #: le fetch unique cible l'URL absolue résolue contre l'origine de la
    #: page: la résolution a eu lieu avant l'envoi au navigateur
    assert len(panel_calls) == 1
    assert '"http://app.test/_profiler/relative-token?panel=db"' in panel_calls[0]["expression"]


def test_collect_rejects_cross_origin_link_before_fetch(mock, client):
    """Un lien profiler pointant vers une autre origine est refusé avant tout
    fetch: un header piégé ne peut pas détourner le navigateur vers un hôte
    attaquant."""
    hit = {**HIT, "link": "https://attacker.example/_profiler/stolen"}

    #: le refus est une erreur explicite nommant l'origine, pas un fetch qui
    #: échouerait silencieusement
    with pytest.raises(ValueError, match="origin rejected"):
        profiler.collect_profiler_report(client, hit, context=PROFILER_CONTEXT, panels=["db"])

    #: aucune commande n'est partie vers le navigateur: le rejet précède
    #: toute action, c'est la garantie de sécurité
    assert mock.commands_for("Runtime.evaluate") == []
