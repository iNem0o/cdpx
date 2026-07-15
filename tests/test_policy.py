from __future__ import annotations

import pytest

from cdpx.policy import (
    Authority,
    ExecutionContext,
    PolicyError,
    assert_authorized,
    assert_loopback_endpoint,
    assert_url_allowed,
    authority_for,
    parse_origins,
    validate_target,
)


def test_execution_context_requires_session_run_target_and_origins():
    """Le contexte d'exécution refuse de se construire à moitié: sans run-id,
    cible attribuée ou origines déclarées, aucune commande ne pourra même
    être évaluée."""
    #: chaque paramètre manquant est refusé avec un message qui nomme le
    #: champ fautif, pour un diagnostic immédiat côté superviseur
    with pytest.raises(PolicyError, match="run-id"):
        ExecutionContext.create(
            run_id="",
            target_id="T1",
            authority="observation",
            origins="http://x.test",
            session_id="S1",
        )
    with pytest.raises(PolicyError, match="target"):
        ExecutionContext.create(
            run_id="R1",
            target_id="",
            authority="observation",
            origins="http://x.test",
            session_id="S1",
        )
    with pytest.raises(PolicyError, match="CDPX_ORIGINS"):
        ExecutionContext.create(
            run_id="R1",
            target_id="T1",
            authority="observation",
            origins="",
            session_id="S1",
        )


def test_origin_patterns_are_canonical_and_fail_closed():
    """Les motifs d'origine sont normalisés (minuscules) et toute forme
    ambiguë — joker total, chemin, credentials, file: — est rejetée: le
    périmètre ne peut pas s'élargir par accident de syntaxe."""
    #: la casse est canonisée pour que la comparaison d'origines soit stable
    assert parse_origins("HTTP://*.TEST,http://localhost:*") == (
        "http://*.test",
        "http://localhost:*",
    )
    for invalid in ("", "*", "*://*", "http://x.test/path", "http://u:p@x.test", "file:///"):
        #: chaque motif trop permissif ou malformé est refusé plutôt
        #: qu'interprété avec indulgence
        with pytest.raises(PolicyError):
            parse_origins(invalid, required=True)


def test_session_origins_apply_to_observation_and_interaction():
    """La liste d'origines de session délimite exactement les hôtes
    joignables: une URL hors périmètre ou non-HTTP est refusée, même pour
    de la simple lecture."""
    patterns = parse_origins("http://*.test,http://127.0.0.1:*")
    #: les URLs conformes aux motifs déclarés passent sans exception
    assert_url_allowed("http://shop.test/page?token=secret", patterns)
    assert_url_allowed("http://127.0.0.1:8899/", patterns)
    #: un hôte hors liste est bloqué avant tout accès réseau
    with pytest.raises(PolicyError, match="origine refusée"):
        assert_url_allowed("https://prod.example/", patterns)
    #: les schémas non HTTP n'ont pas d'origine comparable: refus explicite
    with pytest.raises(PolicyError, match="origine HTTP"):
        assert_url_allowed("about:blank", patterns)


def test_origin_matching_is_structured_for_ipv6_and_wildcard_ports():
    """La comparaison d'origines est structurelle, pas textuelle: IPv6 entre
    crochets, port joker et joker de sous-domaine matchent correctement sans
    laisser passer un hôte voisin qui leur ressemble."""
    ipv6 = parse_origins("http://[::1]:*")
    #: le joker de port couvre tout port, port implicite compris
    assert_url_allowed("http://[::1]:9222/page", ipv6)
    assert_url_allowed("http://[::1]/default-port", ipv6)
    #: une autre adresse IPv6 est refusée malgré sa ressemblance textuelle
    with pytest.raises(PolicyError, match="origine refusée"):
        assert_url_allowed("http://[::2]:9222/page", ipv6)

    subdomains = parse_origins("https://*.example.test")
    #: le joker de sous-domaine matche à toute profondeur
    assert_url_allowed("https://shop.example.test/path", subdomains)
    assert_url_allowed("https://deep.shop.example.test/path", subdomains)
    #: mais jamais le domaine nu: le joker n'inclut pas l'apex
    with pytest.raises(PolicyError, match="origine refusée"):
        assert_url_allowed("https://example.test/path", subdomains)


def test_loopback_validates_discovery_and_published_websocket():
    """Le client ne parle qu'à un Chrome local: l'hôte de découverte ET
    l'endpoint WebSocket qu'il publie doivent tous deux être loopback."""
    #: les formes loopback IPv4, hostname local et IPv6 sont acceptées
    assert_loopback_endpoint("127.0.0.1", "ws://localhost:9333/devtools/page/T1")
    assert_loopback_endpoint("::1", "ws://[::1]:9333/devtools/page/T1")
    #: un hôte de découverte distant est refusé même si le WS annoncé est local
    with pytest.raises(PolicyError, match="loopback"):
        assert_loopback_endpoint("chrome.internal", "ws://127.0.0.1:9222/devtools/page/T1")
    #: un WS publié vers une IP externe est refusé même en découverte locale:
    #: pas de rebond hors machine
    with pytest.raises(PolicyError, match="WebSocket"):
        assert_loopback_endpoint("127.0.0.1", "ws://10.0.0.4:9222/devtools/page/T1")


def test_target_must_be_the_owned_page_target():
    """La session ne pilote que la cible qui lui a été attribuée: bon
    identifiant, type page et endpoint WebSocket présent, sinon refus."""
    target = {
        "id": "T1",
        "type": "page",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/T1",
    }
    #: la cible attribuée et conforme est retournée telle quelle
    assert validate_target(target, "T1") == target
    #: un autre identifiant que celui attribué est refusé: pas de saut d'onglet
    with pytest.raises(PolicyError, match="attribué"):
        validate_target(target, "T2")
    #: un target non-page (worker, extension) est hors périmètre de pilotage
    with pytest.raises(PolicyError, match="type page"):
        validate_target({**target, "type": "service_worker"}, "T1")
    #: sans endpoint WebSocket la cible n'est pas pilotable de façon vérifiable
    with pytest.raises(PolicyError, match="WebSocket"):
        validate_target({"id": "T1", "type": "page"}, "T1")


@pytest.mark.parametrize(
    ("command", "action", "expected"),
    [
        ("goto", None, Authority.OBSERVATION),
        ("text", None, Authority.OBSERVATION),
        ("network", None, Authority.OBSERVATION),
        ("click", None, Authority.INTERACTION),
        ("type", None, Authority.INTERACTION),
        ("key", None, Authority.INTERACTION),
        ("eval", None, Authority.PRIVILEGED),
        ("cookies", ["get"], Authority.PRIVILEGED),
        ("storage", None, Authority.PRIVILEGED),
        ("profiler", None, Authority.PRIVILEGED),
        ("intercept", None, Authority.PRIVILEGED),
        ("emulate", ["goto", "http://x.test"], Authority.PRIVILEGED),
        ("vitals", None, Authority.OBSERVATION),
        ("vitals", ["click", "#go"], Authority.INTERACTION),
        ("record", ["wait", "#ready"], Authority.OBSERVATION),
        ("record", ["click", "#go"], Authority.INTERACTION),
        ("record", ["eval", "1"], Authority.PRIVILEGED),
        ("tabs", ["list"], Authority.OBSERVATION),
    ],
)
def test_command_authority_matrix(command, action, expected):
    """Chaque commande CLI est classée dans une autorité fixe (observation,
    interaction, privileged); les commandes composites héritent de l'autorité
    de l'action qu'elles embarquent."""
    #: la matrice commande -> autorité est le contrat exact que le portail
    #: d'autorisation applique avant toute exécution
    assert authority_for(command, action) is expected


def test_unknown_commands_and_insufficient_grants_fail_closed():
    """Une commande inconnue n'a pas d'autorité implicite et un grant
    insuffisant bloque avant exécution: la politique échoue fermé."""
    #: une commande non classée est refusée au lieu d'hériter d'un défaut
    with pytest.raises(PolicyError, match="non classée"):
        authority_for("future-command")
    context = ExecutionContext.create(
        run_id="R1",
        target_id="T1",
        authority="observation",
        origins="http://*.test",
        session_id="S1",
    )
    #: le grant observation couvre la lecture sans friction
    assert_authorized(context, "text")
    #: interaction et privileged exigent chacun une élévation explicite,
    #: nommée dans l'erreur pour guider le superviseur
    with pytest.raises(PolicyError, match="requiert interaction"):
        assert_authorized(context, "click")
    with pytest.raises(PolicyError, match="requiert privileged"):
        assert_authorized(context, "eval")
