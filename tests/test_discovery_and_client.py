"""Découverte HTTP (/json) et client WebSocket, validés contre le mock CDP."""

import json

import pytest

from cdpx import client as client_module
from cdpx import discovery
from cdpx.client import CDPClient, CDPError, CDPTimeout, CDPTransportError


def _connect(mock) -> CDPClient:
    target_id = next(iter(mock.targets))
    target = discovery.pick_page("127.0.0.1", mock.http_port, target_id)
    return CDPClient(target["webSocketDebuggerUrl"], timeout=5)


# -- découverte ------------------------------------------------------------------


def test_list_targets(mock):
    """La découverte /json expose la page unique du mock avec un endpoint
    WebSocket de débogage strictement loopback, prêt pour le client."""
    targets = discovery.list_targets("127.0.0.1", mock.http_port)
    #: une seule cible de type page est découvrable, et son URL de pilotage
    #: reste confinée à l'interface loopback
    assert len(targets) == 1
    assert targets[0]["type"] == "page"
    assert targets[0]["webSocketDebuggerUrl"].startswith("ws://127.0.0.1:")


def test_version(mock):
    """/json/version annonce la version de protocole CDP que le client
    sait parler — le contrat minimal avant tout dialogue WebSocket."""
    v = discovery.version("127.0.0.1", mock.http_port)
    #: la version annoncée correspond au protocole implémenté par le client
    assert v["Protocol-Version"] == "1.3"


def test_loopback_discovery_ignores_environment_proxy(mock, monkeypatch):
    """Un proxy hostile déclaré dans l'environnement ne détourne jamais le
    trafic de découverte: les appels /json restent en connexion directe."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    #: la découverte aboutit alors qu'un proxy injoignable est imposé par
    #: l'environnement, preuve que le loopback le contourne
    assert discovery.version("127.0.0.1", mock.http_port)["Protocol-Version"] == "1.3"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('{"not": "a list"}', "tableau JSON requis"),
        ('[{"type": "page"}]', "sans id valide"),
        ("not-json", "JSON invalide"),
    ],
)
def test_list_targets_rejects_malformed_discovery_shapes(monkeypatch, payload, message):
    monkeypatch.setattr(discovery, "_http", lambda *_args, **_kwargs: payload)

    with pytest.raises(discovery.DiscoveryError, match=message):
        discovery.list_targets("127.0.0.1", 9222)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('[{"id":"T1","url":3}]', "target\\[0\\]\\.url textuel requis"),
        (
            '[{"id":"T1","webSocketDebuggerUrl":false}]',
            "webSocketDebuggerUrl textuel requis",
        ),
    ],
)
def test_list_targets_rejects_non_string_declared_fields(monkeypatch, payload, message):
    monkeypatch.setattr(discovery, "_http", lambda *_args, **_kwargs: payload)

    with pytest.raises(discovery.DiscoveryError, match=message):
        discovery.list_targets("127.0.0.1", 9222)


def test_version_rejects_non_string_declared_fields(monkeypatch):
    monkeypatch.setattr(discovery, "_http", lambda *_args, **_kwargs: '{"Browser":3}')

    with pytest.raises(discovery.DiscoveryError, match="version\\.Browser textuel requis"):
        discovery.version("127.0.0.1", 9222)


def test_version_decode_error_keeps_http_context(monkeypatch):
    monkeypatch.setattr(discovery, "_http", lambda *_args, **_kwargs: "not-json")

    with pytest.raises(discovery.DiscoveryError, match="JSON invalide") as error:
        discovery.version("127.0.0.1", 9222)

    assert error.value.method == "GET"
    assert error.value.url == "http://127.0.0.1:9222/json/version"
    assert isinstance(error.value.__cause__, json.JSONDecodeError)


def test_list_decode_error_keeps_http_context(monkeypatch):
    monkeypatch.setattr(discovery, "_http", lambda *_args, **_kwargs: "not-json")

    with pytest.raises(discovery.DiscoveryError, match="JSON invalide") as error:
        discovery.list_targets("127.0.0.1", 9222)

    assert error.value.method == "GET"
    assert error.value.url == "http://127.0.0.1:9222/json/list"
    assert isinstance(error.value.__cause__, json.JSONDecodeError)


def test_new_activate_close_tab(mock):
    """Le cycle de vie complet d'un onglet — création sur une URL, activation,
    fermeture — passe par l'API HTTP /json et laisse l'inventaire cohérent."""
    tab = discovery.new_tab("127.0.0.1", mock.http_port, "http://example.test/x")
    #: l'onglet naît sur l'URL demandée et s'ajoute à l'inventaire des cibles
    assert tab["url"] == "http://example.test/x"
    assert len(discovery.list_targets("127.0.0.1", mock.http_port)) == 2
    discovery.activate_tab("127.0.0.1", mock.http_port, tab["id"])
    discovery.close_tab("127.0.0.1", mock.http_port, tab["id"])
    #: la fermeture retire réellement la cible: retour à l'état initial
    assert len(discovery.list_targets("127.0.0.1", mock.http_port)) == 1


def test_new_tab_falls_back_to_get_only_for_method_not_allowed(monkeypatch):
    calls = []

    def legacy_http(_host, _port, _path, method="GET"):
        calls.append(method)
        if method == "PUT":
            raise discovery.DiscoveryError("PUT rejected", status=405)
        return '{"id":"legacy"}'

    monkeypatch.setattr(discovery, "_http", legacy_http)

    assert discovery.new_tab("127.0.0.1", 9222)["id"] == "legacy"
    assert calls == ["PUT", "GET"]


def test_new_tab_preserves_non_compatibility_put_failure(monkeypatch):
    calls = []

    def failed_http(_host, _port, _path, method="GET"):
        calls.append(method)
        raise discovery.DiscoveryError("browser unavailable", status=503)

    monkeypatch.setattr(discovery, "_http", failed_http)

    with pytest.raises(discovery.DiscoveryError, match="browser unavailable") as error:
        discovery.new_tab("127.0.0.1", 9222)

    assert error.value.status == 503
    assert calls == ["PUT"]


def test_new_tab_malformed_put_response_does_not_trigger_legacy_fallback(monkeypatch):
    calls = []

    def malformed_http(_host, _port, _path, method="GET"):
        calls.append(method)
        return "not-json"

    monkeypatch.setattr(discovery, "_http", malformed_http)

    with pytest.raises(discovery.DiscoveryError, match="JSON invalide") as error:
        discovery.new_tab("127.0.0.1", 9222)

    assert calls == ["PUT"]
    assert error.value.method == "PUT"
    assert error.value.url == "http://127.0.0.1:9222/json/new"
    assert isinstance(error.value.__cause__, json.JSONDecodeError)


def test_new_tab_reports_both_failed_compatibility_attempts(monkeypatch):
    def failed_http(_host, _port, _path, method="GET"):
        status = 405 if method == "PUT" else 500
        raise discovery.DiscoveryError(f"{method} failed", status=status)

    monkeypatch.setattr(discovery, "_http", failed_http)

    with pytest.raises(discovery.DiscoveryError, match="PUT failed.*GET failed") as error:
        discovery.new_tab("127.0.0.1", 9222)

    assert error.value.status == 500


def test_new_tab_malformed_legacy_get_response_keeps_fallback_context(monkeypatch):
    def legacy_http(_host, _port, _path, method="GET"):
        if method == "PUT":
            raise discovery.DiscoveryError("PUT rejected", status=405)
        return "not-json"

    monkeypatch.setattr(discovery, "_http", legacy_http)

    with pytest.raises(discovery.DiscoveryError, match="legacy GET fallback failed") as error:
        discovery.new_tab("127.0.0.1", 9222)

    assert error.value.method == "GET"
    assert error.value.url == "http://127.0.0.1:9222/json/new"
    assert isinstance(error.value.__cause__, discovery.DiscoveryError)
    assert isinstance(error.value.__cause__.__cause__, json.JSONDecodeError)


def test_pick_page_by_id_and_missing(mock):
    """pick_page résout une cible par identifiant exact et refuse un
    identifiant inconnu au lieu de se rabattre sur une autre page."""
    tid = next(iter(mock.targets))
    #: l'identifiant demandé est résolu tel quel, sans substitution
    assert discovery.pick_page("127.0.0.1", mock.http_port, tid)["id"] == tid
    #: une cible absente lève l'erreur de découverte dédiée plutôt qu'un
    #: repli silencieux vers une page arbitraire
    with pytest.raises(discovery.DiscoveryError):
        discovery.pick_page("127.0.0.1", mock.http_port, "NOPE")


# -- client ---------------------------------------------------------------------


def test_send_and_result(mock, evidence_case):
    """Un aller-retour commande/réponse aboutit et la trame émise sur le fil
    est exactement celle demandée: sortie ET protocole sont prouvés."""
    with _connect(mock) as c:
        #: la réponse (vide) du domaine activé revient corrélée à l'appel
        assert c.send("Page.enable") == {}
    #: côté fil, le mock a reçu une unique commande sans paramètre — c'est
    #: le protocole réellement émis qui est jugé, pas seulement le retour
    assert mock.commands_for("Page.enable") == [{}]

    if evidence_case is not None:
        # Preuve du protocole émis: la trace mock des commandes Page.enable.
        evidence_case.attach_json(
            "Trace protocolaire du mock (Page.enable)",
            {"Page.enable": mock.commands_for("Page.enable")},
        )


def test_connection_failure_is_wrapped_with_endpoint_context(monkeypatch):
    def fail_connect(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(client_module, "connect", fail_connect)

    with pytest.raises(CDPTransportError, match="connexion CDP impossible.*ws://x.test") as error:
        CDPClient("ws://x.test/devtools/page/T1")

    assert isinstance(error.value.__cause__, OSError)


def test_send_failure_is_wrapped_with_method_context():
    class BrokenSocket:
        def send(self, _payload):
            raise OSError("connection closed")

    client = object.__new__(CDPClient)
    client.ws_url = "ws://x.test/devtools/page/T1"
    client.timeout = 5
    client._id = 0
    client.events = []
    client._responses = {}
    client._ws = BrokenSocket()

    with pytest.raises(CDPTransportError, match="envoi Page.enable") as error:
        client.send_nowait("Page.enable")

    assert isinstance(error.value.__cause__, OSError)


def test_send_nowait_allows_event_before_command_response(mock):
    """L'envoi sans attente laisse consommer un évènement arrivé avant la
    réponse de commande — condition nécessaire à l'interception réseau, où
    l'évènement Fetch précède la fin de la navigation."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {"requestId": "I1", "request": {"url": "http://x.test/"}},
            }
        ]
    )
    with _connect(mock) as c:
        c.send_nowait("Page.navigate", {"url": "http://x.test/"})
        ev = c.next_event(timeout=2)
    #: l'évènement d'interception est lisible avant la réponse de navigation,
    #: ce qu'un envoi bloquant rendrait impossible (deadlock d'interception)
    assert ev["method"] == "Fetch.requestPaused"
    #: la commande de navigation a néanmoins bien été émise sur le fil
    assert mock.commands_for("Page.navigate") == [{"url": "http://x.test/"}]


def test_wait_response_survives_event_consumption(mock):
    """La réponse d'une commande reste corrélée par identifiant même quand
    des évènements sont consommés entre l'envoi et wait_response: rien ne se
    perd dans l'entrelacement évènements/réponses."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {"requestId": "I1", "request": {"url": "http://x.test/"}},
            }
        ]
    )
    with _connect(mock) as c:
        command_id = c.send_nowait("Page.navigate", {"url": "http://x.test/"})
        #: un évènement intermédiaire est consommé en premier, sans que cela
        #: détruise la réponse encore en attente
        assert c.next_event(timeout=1)["method"] == "Fetch.requestPaused"
        response = c.wait_response(command_id)
        #: la réponse retrouvée après coup porte bien les identifiants de
        #: frame scriptés pour cette navigation précise
        assert response["frameId"] == "FRAME1" and response["loaderId"] == "LOADER1"


def test_cdp_error_raised(mock):
    """Une erreur protocolaire renvoyée par le navigateur devient une
    CDPError typée qui conserve le code JSON-RPC d'origine, jamais un
    résultat vide silencieux."""
    #: une méthode inconnue déclenche l'exception dédiée au protocole
    with _connect(mock) as c, pytest.raises(CDPError) as exc:
        c.send("Bogus.method")
    #: le code JSON-RPC «méthode introuvable» survit jusqu'au diagnostic
    assert exc.value.code == -32601


def test_events_buffered_then_waited(mock):
    """Attendre un évènement précis ne détruit pas ceux arrivés avant lui:
    le buffer permet de les consommer après coup, dans n'importe quel ordre."""
    with _connect(mock) as c:
        c.send("Page.navigate", {"url": "http://x.test/"})
        ev = c.wait_event("Page.loadEventFired", timeout=2)
        #: l'attente ciblée saute par-dessus domContentEventFired et retrouve
        #: bien l'évènement load scripté par le mock
        assert ev["params"]["timestamp"] == 1.2
        # domContentEventFired est resté dans le buffer, consommable après coup
        ev2 = c.wait_event("Page.domContentEventFired", timeout=0.5)
        #: l'évènement antérieur, non réclamé au premier passage, est toujours
        #: disponible — preuve que rien n'a été jeté en route
        assert ev2["params"]["timestamp"] == 1.0


def test_wait_event_preserves_interleaved_command_response(mock):
    with _connect(mock) as client:
        command_id = client.send_nowait("Page.navigate", {"url": "http://x.test/"})

        event = client.wait_event("Page.loadEventFired", timeout=2)
        response = client.wait_response(command_id, timeout=0.1)

    assert event["method"] == "Page.loadEventFired"
    assert response == {"frameId": "FRAME1", "loaderId": "LOADER1"}


def test_wait_event_timeout(mock):
    """L'attente d'un évènement qui ne vient jamais échoue en temps borné
    par une exception dédiée: pas de blocage possible du CLI."""
    #: sans navigation, aucun load n'arrive: le délai court lève CDPTimeout
    #: au lieu de suspendre indéfiniment l'appelant
    with _connect(mock) as c, pytest.raises(CDPTimeout):
        c.wait_event("Page.loadEventFired", timeout=0.3)


def test_zero_timeout_is_immediate_for_commands_responses_and_events(mock):
    with _connect(mock) as client, pytest.raises(CDPTimeout):
        client.send("Page.enable", timeout=0)

    with _connect(mock) as client:
        command_id = client.send_nowait("Page.enable")
        with pytest.raises(CDPTimeout):
            client.wait_response(command_id, timeout=0)

    with _connect(mock) as client, pytest.raises(CDPTimeout):
        client.wait_event("Page.loadEventFired", timeout=0)

    with _connect(mock) as client, pytest.raises(CDPTimeout):
        client.next_event(timeout=0)


def test_negative_timeouts_are_rejected_before_io(mock):
    with _connect(mock) as client:
        before = len(mock.commands)
        with pytest.raises(ValueError, match="timeout"):
            client.send("Page.enable", timeout=-0.1)
        with pytest.raises(ValueError, match="timeout"):
            client.wait_response(999, timeout=-0.1)
        with pytest.raises(ValueError, match="timeout"):
            client.wait_event("Page.loadEventFired", timeout=-0.1)
        with pytest.raises(ValueError, match="timeout"):
            client.next_event(timeout=-0.1)
        assert len(mock.commands) == before


def test_collect_events_filters_and_drains(mock):
    """La collecte fenêtrée ne retient que les méthodes demandées et vide le
    buffer au passage: aucun évènement ne fuit vers la commande suivante."""
    mock.script_console([{"type": "log", "args": [{"type": "string", "value": "x"}]}])
    with _connect(mock) as c:
        c.send("Runtime.enable")
        got = c.collect_events(0.3, ("Runtime.consoleAPICalled",))
        #: seul l'évènement console scripté franchit le filtre de méthodes
        assert len(got) == 1
        #: le buffer interne ressort vide: la fenêtre d'écoute a tout drainé
        assert c.events == []


def test_collect_events_preserves_interleaved_command_response(mock):
    with _connect(mock) as client:
        command_id = client.send_nowait("Page.enable")

        assert client.collect_events(0.05) == []
        assert client.wait_response(command_id, timeout=0) == {}


def test_collect_events_rejects_negative_duration_without_draining(mock):
    with _connect(mock) as client:
        client.events = [{"method": "Runtime.consoleAPICalled", "params": {}}]

        with pytest.raises(ValueError, match="durée de collecte"):
            client.collect_events(-0.1)

        assert len(client.events) == 1


@pytest.mark.parametrize(
    ("received", "message"),
    [
        (OSError("connection closed"), "transport interrompu"),
        ("not-json", "message CDP invalide"),
        ("[]", "objet CDP requis"),
    ],
)
def test_collect_events_surfaces_transport_and_frame_failures(received, message):
    """Une collecte interrompue n'est jamais présentée comme un succès partiel."""

    class ScriptedSocket:
        def recv(self, *, timeout):
            if isinstance(received, Exception):
                raise received
            return received

    client = object.__new__(CDPClient)
    client.events = [{"method": "Runtime.consoleAPICalled", "params": {}}]
    client._ws = ScriptedSocket()

    with pytest.raises(CDPTransportError, match=message):
        client.collect_events(0.1)

    assert len(client.events) == 1


@pytest.mark.parametrize(
    ("received", "message"),
    [
        ('{"method":3,"params":{}}', "évènement CDP invalide"),
        (
            '{"method":"Page.loadEventFired","params":[]}',
            "params CDP invalides",
        ),
        ('{"id":"1","result":{}}', "réponse CDP sans id valide"),
        ('{"id":1,"result":[]}', "result CDP invalide"),
        (
            '{"id":1,"error":{"code":"x","message":3}}',
            "error CDP mal formée",
        ),
        (
            '{"id":1,"result":{},"error":{"code":1,"message":"x"}}',
            "réponse CDP ambiguë",
        ),
        (
            '{"method":"Page.loadEventFired","result":{}}',
            "évènement CDP invalide",
        ),
    ],
)
def test_collect_events_rejects_malformed_cdp_envelopes(received, message):
    class ScriptedSocket:
        def recv(self, *, timeout):
            return received

    client = object.__new__(CDPClient)
    client.events = []
    client._responses = {}
    client._ws = ScriptedSocket()

    with pytest.raises(CDPTransportError, match=message):
        client.collect_events(0.1)
