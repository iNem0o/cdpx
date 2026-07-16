"""Chaque primitive validée contre le mock: on vérifie à la fois la SORTIE
(contrat JSON stable) et le PROTOCOLE émis (méthodes/params enregistrés)."""

import json
import pathlib
import stat

import pytest

from cdpx import discovery
from cdpx.client import CDPClient, CDPTimeout
from cdpx.primitives import advanced, audit, capture, dev, inputs, js, nav, net, state
from cdpx.security import RedactionContext

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture()
def client(mock):
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    target = discovery.pick_page("127.0.0.1", mock.http_port, target_id)
    with CDPClient(target["webSocketDebuggerUrl"], timeout=5) as c:
        yield c


# -- nav --------------------------------------------------------------------------


def test_navigate_waits_load(mock, client):
    """La navigation ne rend la main qu'après l'évènement load et n'émet
    qu'un seul Page.navigate, vers l'URL demandée telle quelle."""
    res = nav.navigate(client, "http://site.test/page", wait="load")
    #: le succès expose le frame réellement navigué, preuve que load a été attendu
    assert res["ok"] is True and res["frameId"] == "FRAME1"
    #: côté protocole, une navigation unique part avec l'URL non altérée
    assert mock.commands_for("Page.navigate") == [{"url": "http://site.test/page"}]


def test_wait_for_polls_until_found(mock, client):
    """wait_for sonde le DOM à intervalle régulier et s'arrête dès que le
    sélecteur apparaît, sans sondage superflu ensuite."""
    mock.on_eval("querySelector", False, False, True)
    res = nav.wait_for(client, "#late-content", timeout=2, poll=0.01)
    #: trois sondages pour trois réponses scriptées: la boucle cesse dès la
    #: première apparition de l'élément
    assert res["found"] is True
    assert len(mock.commands_for("Runtime.evaluate")) == 3


def test_wait_for_times_out(mock, client):
    """Un sélecteur qui n'apparaît jamais lève CDPTimeout à l'échéance au lieu
    de bloquer la session indéfiniment."""
    mock.on_eval("querySelector", False)
    #: le budget temps transforme l'absence en erreur explicite, jamais en attente infinie
    with pytest.raises(CDPTimeout):
        nav.wait_for(client, "#never", timeout=0.15, poll=0.02)


def test_wait_for_visible_polls_until_element_has_a_non_zero_box(mock, client):
    """wait_for_visible exige plus que la présence DOM: la sonde injectée
    vérifie connexion, styles non masquants et boîte non nulle avant de
    déclarer l'élément visible."""
    mock.on_eval("__cdpx_visible", False, False, True)

    res = nav.wait_for_visible(client, "#late-content", timeout=2, poll=0.01)

    #: la visibilité est confirmée pour le sélecteur demandé, pas un autre
    assert res["visible"] is True
    assert res["selector"] == "#late-content"
    calls = mock.commands_for("Runtime.evaluate")
    #: la boucle a re-sondé jusqu'au basculement d'état, puis s'est arrêtée
    assert len(calls) == 3
    expression = calls[0]["expression"]
    #: la sonde couvre tous les modes de masquage CSS et la géométrie réelle,
    #: pas seulement la présence d'un noeud dans le DOM
    assert ".isConnected" in expression
    assert 'style.display === "none"' in expression
    assert 'style.visibility === "hidden"' in expression
    assert 'style.visibility === "collapse"' in expression
    assert "rect.width > 0 && rect.height > 0" in expression


def test_wait_for_visible_times_out_while_element_stays_hidden(mock, client):
    """Un élément durablement masqué fait lever CDPTimeout avec un diagnostic
    'non visible', distinct de l'absence pure du DOM."""
    mock.on_eval("__cdpx_visible", False)

    #: le message distingue l'invisibilité persistante d'un sélecteur introuvable
    with pytest.raises(CDPTimeout, match="non visible"):
        nav.wait_for_visible(client, "#hidden", timeout=0.15, poll=0.02)


# -- js ---------------------------------------------------------------------------


def test_evaluate_value_and_exception(mock, client):
    """evaluate restitue la valeur JS brute et convertit les exceptionDetails
    du protocole en JSException Python porteuse du message d'origine."""
    mock.on_eval("1 + 1", 2)
    #: la valeur calculée traverse sans enveloppe ni conversion
    assert js.evaluate(client, "1 + 1") == 2
    mock.on_eval("boom", {"raw": {"exceptionDetails": {"text": "ReferenceError: boom"}}})
    #: une erreur JS devient une exception typée côté Python, message conservé
    with pytest.raises(js.JSException, match="boom"):
        js.evaluate(client, "boom()")


def test_get_text_and_html_and_count(mock, client):
    """Chaque lecteur DOM (texte, HTML, comptage) enveloppe la valeur évaluée
    dans la clé de son contrat JSON, sans transformation du contenu."""
    mock.on_eval("innerText", "Bonjour")
    #: chaque primitive de lecture rend la valeur de la page sous sa clé contractuelle
    assert js.get_text(client, "#intro")["text"] == "Bonjour"
    mock.on_eval("outerHTML", "<p>x</p>")
    assert js.get_html(client, "p")["html"] == "<p>x</p>"
    mock.on_eval("querySelectorAll", 3)
    assert js.count(client, "h1")["count"] == 3


# -- inputs -----------------------------------------------------------------------


ACTIONABLE = {
    "attached": True,
    "visible": True,
    "enabled": True,
    "stable": True,
    "receives_events": True,
    "editable": True,
    "rect": {"x": 10, "y": 20, "width": 100, "height": 30},
}


@pytest.mark.scenario(
    feature="dom-interaction",
    journey="submit-form",
    scenario_id="dom-interaction.submit-form-like-user",
    proves=["Le clic émet la séquence souris moved/pressed/released au centre de l'élément."],
)
def test_click_dispatches_mouse_events_at_center(mock, client, evidence_case):
    """Un clic sonde d'abord l'actionnabilité puis émet la séquence souris de
    confiance moved/pressed/released, visée au centre géométrique de
    l'élément."""
    mock.on_eval("__cdpx_actionability", json.dumps(ACTIONABLE))

    res = inputs.click(client, "#submit-btn")

    #: le point rapporté est le centre exact du rect sondé, pas un coin
    assert (res["x"], res["y"]) == (60.0, 35.0)
    #: la séquence Input complète imite un utilisateur réel, chaque évènement
    #: au même point et au même bouton
    assert mock.commands_for("Input.dispatchMouseEvent") == [
        {
            "type": "mouseMoved",
            "x": 60.0,
            "y": 35.0,
            "button": "left",
            "clickCount": 1,
        },
        {
            "type": "mousePressed",
            "x": 60.0,
            "y": 35.0,
            "button": "left",
            "clickCount": 1,
        },
        {
            "type": "mouseReleased",
            "x": 60.0,
            "y": 35.0,
            "button": "left",
            "clickCount": 1,
        },
    ]
    (probe,) = mock.commands_for("Runtime.evaluate")
    #: la sonde est une promesse attendue dont la valeur revient sérialisée
    assert probe["awaitPromise"] is True
    assert probe["returnByValue"] is True
    expression = probe["expression"]
    #: la sonde vérifie stabilité (double rAF), désactivation, inertie et
    #: hit-testing du point de clic avant d'autoriser le moindre évènement
    assert expression.count("requestAnimationFrame") == 2
    assert '.matches(":disabled")' in expression
    assert "aria-disabled" in expression
    assert '.closest("[inert]")' in expression
    assert "pointerEvents" in expression
    assert "document.elementFromPoint" in expression
    assert "element.contains(hit)" in expression

    # Preuve secondaire: le journal des évènements Input émis atteste la séquence
    # souris de confiance visée au centre géométrique de l'élément.
    if evidence_case is not None:
        evidence_case.attach_json(
            "Séquence Input.dispatchMouseEvent du clic (centre 60,35)",
            {
                "point": {"x": res["x"], "y": res["y"]},
                "mouse_events": mock.commands_for("Input.dispatchMouseEvent"),
            },
        )


def test_click_element_not_found(mock, client):
    """Un sélecteur détaché du DOM fait échouer le clic en ElementNotFound
    sans qu'aucun évènement souris n'atteigne la page."""
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, "attached": False}))

    #: l'absence est signalée par une exception dédiée au message exploitable
    with pytest.raises(inputs.ElementNotFound, match="sélecteur introuvable"):
        inputs.click(client, "#ghost")
    #: fail-closed: malgré l'échec, la page n'a reçu aucun évènement souris
    assert mock.commands_for("Input.dispatchMouseEvent") == []


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({"visible": False}, "non visible"),
        ({"enabled": False}, "désactivé"),
        ({"stable": False}, "instable"),
        ({"receives_events": False}, "recouvert"),
    ],
)
def test_click_refuses_non_actionable_element_without_input(mock, client, state, message):
    """Chaque défaut d'actionnabilité (invisible, désactivé, instable,
    recouvert) bloque le clic avec son diagnostic propre, avant toute
    émission souris."""
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, **state}))

    #: le refus nomme précisément le défaut rencontré, quel que soit le cas
    with pytest.raises(inputs.ElementNotInteractable, match=message):
        inputs.click(client, "#blocked")

    #: le garde-fou agit avant le protocole: la page ne voit rien passer
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_type_text_clear_selects_then_deletes_through_input_domain(mock, client):
    """--clear vide le champ par sélection puis Backspace via le domaine
    Input — jamais par affectation de value — et la sortie masque le texte
    tapé."""
    mock.on_eval("__cdpx_actionability", json.dumps(ACTIONABLE))
    mock.on_eval("__cdpx_prepare_text", True)

    res = inputs.type_text(client, "#name", "Léo", clear=True)

    #: la sortie confirme frappe et nettoyage sans jamais reproduire le texte saisi
    assert res == {
        "typed": True,
        "value_masked": True,
        "selector": "#name",
        "cleared": True,
    }
    assert "Léo" not in json.dumps(res, ensure_ascii=False)
    evaluations = mock.commands_for("Runtime.evaluate")
    #: deux évaluations seulement: sonde d'actionnabilité puis préparation du champ
    assert len(evaluations) == 2
    prepare = evaluations[1]["expression"]
    #: la préparation sélectionne le contenu via l'API DOM, sans affectation
    #: directe de value qui contournerait les frameworks réactifs
    assert "el.select()" in prepare
    assert "range.selectNodeContents(el)" in prepare
    assert "selection.removeAllRanges()" in prepare
    assert "el.value =" not in prepare
    #: l'effacement est une vraie frappe Backspace du domaine Input
    assert mock.commands_for("Input.dispatchKeyEvent") == [
        {
            "type": "rawKeyDown",
            "key": "Backspace",
            "code": "Backspace",
            "windowsVirtualKeyCode": 8,
        },
        {
            "type": "keyUp",
            "key": "Backspace",
            "code": "Backspace",
            "windowsVirtualKeyCode": 8,
        },
    ]
    #: le texte entre par insertText, comme une saisie de confiance
    assert mock.commands_for("Input.insertText") == [{"text": "Léo"}]
    methods = [method for _, method, _ in mock.commands]
    #: l'ordre du protocole est déterministe: sondes, effacement, puis insertion
    assert methods == [
        "Runtime.evaluate",
        "Runtime.evaluate",
        "Input.dispatchKeyEvent",
        "Input.dispatchKeyEvent",
        "Input.insertText",
    ]


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({"visible": False}, "non visible"),
        ({"enabled": False}, "désactivé"),
        ({"editable": False}, "non éditable"),
    ],
)
def test_type_text_refuses_invalid_target_without_input(mock, client, state, message):
    """Un champ invisible, désactivé ou non éditable bloque la frappe avec
    son diagnostic dédié, sans qu'aucune touche ni insertion n'atteigne la
    page."""
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, **state}))

    #: le diagnostic nomme le défaut précis qui empêche la saisie
    with pytest.raises(inputs.ElementNotInteractable, match=message):
        inputs.type_text(client, "#blocked", "secret", clear=True)

    #: rien n'a été tapé: ni évènement clavier ni insertion de texte
    assert mock.commands_for("Input.dispatchKeyEvent") == []
    assert mock.commands_for("Input.insertText") == []


def test_type_text_refuses_missing_target_without_input(mock, client):
    """Un sélecteur absent du DOM fait échouer la frappe en ElementNotFound —
    distinct de la non-interactivité — et le clavier reste muet."""
    mock.on_eval("__cdpx_actionability", json.dumps({**ACTIONABLE, "attached": False}))

    #: l'absence est distinguée de la non-interactivité par le type d'exception
    with pytest.raises(inputs.ElementNotFound, match="sélecteur introuvable"):
        inputs.type_text(client, "#missing", "secret", clear=True)

    #: aucune saisie même partielle ne s'échappe vers la page
    assert mock.commands_for("Input.dispatchKeyEvent") == []
    assert mock.commands_for("Input.insertText") == []


def test_press_key_enter_sequence(mock, client):
    """Enter, touche imprimante, émet la séquence complète avec évènement
    char; une touche hors registre est rejetée avant toute émission."""
    inputs.press_key(client, "Enter")
    keys = mock.commands_for("Input.dispatchKeyEvent")
    #: Enter produit un caractère: l'évènement char est intercalé dans la séquence
    assert [k["type"] for k in keys] == ["rawKeyDown", "char", "keyUp"]
    #: une touche absente du registre supporté est refusée d'emblée
    with pytest.raises(ValueError):
        inputs.press_key(client, "F13")


def test_press_key_backspace_sequence(mock, client):
    """Backspace, touche sans glyphe, n'émet que rawKeyDown/keyUp: aucun
    évènement char parasite ne doit polluer le champ."""
    result = inputs.press_key(client, "Backspace")

    #: une touche d'édition sans caractère ne génère pas d'évènement char
    assert result == {"pressed": "Backspace"}
    assert [event["type"] for event in mock.commands_for("Input.dispatchKeyEvent")] == [
        "rawKeyDown",
        "keyUp",
    ]


@pytest.mark.parametrize(
    ("key", "types"),
    [
        ("Space", ["rawKeyDown", "char", "keyUp"]),
        ("Delete", ["rawKeyDown", "keyUp"]),
        ("Home", ["rawKeyDown", "keyUp"]),
        ("ArrowLeft", ["rawKeyDown", "keyUp"]),
        ("ArrowRight", ["rawKeyDown", "keyUp"]),
        ("PageDown", ["rawKeyDown", "keyUp"]),
    ],
)
def test_press_key_supports_common_navigation_and_editing_keys(mock, client, key, types):
    """Le registre couvre les touches de navigation et d'édition courantes,
    chacune avec sa séquence exacte: l'évènement char n'apparaît que pour les
    touches qui impriment."""
    #: la séquence émise correspond à la nature de la touche (avec ou sans glyphe)
    assert inputs.press_key(client, key) == {"pressed": key}
    assert [event["type"] for event in mock.commands_for("Input.dispatchKeyEvent")] == types


# -- capture ----------------------------------------------------------------------


def test_screenshot_writes_valid_png(mock, client, tmp_path):
    """La capture écrit un vrai PNG protégé en 0600 et propage full_page
    jusqu'au paramètre captureBeyondViewport du protocole."""
    out = tmp_path / "shot.png"
    res = capture.screenshot(client, str(out), full_page=True)
    #: le fichier est un PNG réel, privé (0600), et son poids est rapporté
    assert out.read_bytes().startswith(b"\x89PNG")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert res["bytes"] > 0
    #: full_page se traduit bien en captureBeyondViewport côté protocole
    assert mock.commands_for("Page.captureScreenshot")[0]["captureBeyondViewport"] is True


def test_pdf_writes_valid_signature(mock, client, tmp_path):
    """L'export PDF écrit un fichier à signature %PDF valide et le protège en
    0600 comme toute preuve produite."""
    out = tmp_path / "page.pdf"
    capture.pdf(client, str(out))
    #: signature de format réelle et permissions privées, comme pour le PNG
    assert out.read_bytes().startswith(b"%PDF")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_console_capture_normalizes_entries(mock, client):
    """La capture console agrège les évènements bruts en entrées normalisées
    (kind/type/text/ts) et compte les erreurs séparément du volume total."""
    mock.script_console(
        [
            {
                "type": "log",
                "args": [
                    {"type": "string", "value": "fixture-log"},
                    {"type": "number", "value": 42},
                ],
                "timestamp": 10.0,
            },
            {
                "type": "error",
                "args": [{"type": "string", "value": "fixture-error"}],
                "timestamp": 11.0,
            },
        ]
    )
    res = capture.console_capture(client, duration=0.3)
    #: le comptage distingue le volume total des seules erreurs
    assert res["count"] == 2 and res["errors"] == 1
    #: des args hétérogènes (chaîne + nombre) sont aplatis en un texte unique horodaté
    assert res["entries"][0] == {
        "kind": "console",
        "type": "log",
        "text": "fixture-log 42",
        "ts": 10.0,
    }


def test_console_follow_yields_ndjson_ready_entries(mock, client):
    """Le mode follow produit des entrées directement sérialisables en NDJSON,
    au même format que la capture ponctuelle."""
    mock.script_console(
        [
            {
                "type": "warn",
                "args": [{"type": "string", "value": "fixture-warn"}],
                "timestamp": 12.0,
            }
        ]
    )
    entries = list(capture.console_follow(client, max_entries=1))
    #: chaque entrée du flux est déjà une ligne NDJSON conforme au contrat
    assert entries == [{"kind": "console", "type": "warn", "text": "fixture-warn", "ts": 12.0}]


@pytest.mark.scenario(
    feature="state-session",
    journey="read-session",
    scenario_id="state-session.redact-sensitive-session-data",
    proves=["Secret, Bearer, JWT et credentials d'URL sont absents de la sortie console."],
)
def test_console_entries_redact_credentials_tokens_and_sensitive_urls(evidence_case):
    """Aucun secret ne survit dans la sortie console: secret enregistré, jeton
    Bearer, JWT et credentials/query d'URL sont tous redactés, et la
    redaction se déclare dans le rapport."""
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123"
    context = RedactionContext.from_secrets(["registered-secret"])
    events = [
        {
            "method": "Runtime.consoleAPICalled",
            "params": {
                "type": "log",
                "args": [
                    {"value": "registered-secret"},
                    {"value": "Bearer bearer-secret"},
                    {
                        "value": (
                            "https://alice:password@example.test/callback?code=secret#fragment"
                        )
                    },
                ],
                "timestamp": 1.0,
            },
        },
        {
            "method": "Runtime.exceptionThrown",
            "params": {
                "exceptionDetails": {"exception": {"description": f"failure jwt={jwt}"}},
                "timestamp": 2.0,
            },
        },
    ]

    entries = list(capture.console_entries(events, context=context))
    serialized = json.dumps(entries)

    #: ni la valeur secrète enregistrée, ni le jeton Bearer, ni le JWT, ni les
    #: identifiants d'URL n'atteignent la sortie sérialisée
    assert "registered-secret" not in serialized
    assert "bearer-secret" not in serialized
    assert jwt not in serialized
    assert "alice" not in serialized and "password" not in serialized
    #: l'URL reste lisible: seul le paramètre sensible est remplacé par le marqueur
    assert "https://example.test/callback?code=***" in entries[0]["text"]
    #: la redaction s'auto-déclare dans le rapport, preuve qu'elle a bien agi
    assert context.report.redacted is True

    # Preuve secondaire: la sortie console déjà redactée, où aucun canari ne figure.
    if evidence_case is not None:
        evidence_case.attach_json("Entrées console redactées (aucun secret)", entries)


# -- net --------------------------------------------------------------------------


@pytest.mark.scenario(
    feature="browser-capture-observability",
    journey="inspect-runtime",
    scenario_id="browser-capture-observability.inspect-runtime-failures",
    proves=["La capture réseau résume total/échecs/erreurs/octets avec URLs masquées."],
)
def test_network_capture_assembles_requests(mock, client, evidence_case):
    """La capture réseau corrèle requête/réponse/fin par requestId, résume
    échecs, erreurs HTTP et octets, et masque credentials et tokens dans
    toutes les URLs de sortie."""
    navigation_url = "http://browser:password@s.test/network.html?token=navigation-secret#fragment"
    mock.script_network(
        [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R1",
                    "type": "Fetch",
                    "request": {
                        "url": "http://alice:password@s.test/api/json?token=one&token=two#part",
                        "method": "GET",
                    },
                },
            },
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": "http://alice:password@s.test/api/json?token=three#response",
                        "status": 200,
                        "mimeType": "application/json",
                    },
                },
            },
            {
                "method": "Network.loadingFinished",
                "params": {"requestId": "R1", "encodedDataLength": 123},
            },
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R2",
                    "type": "Fetch",
                    "request": {"url": "http://s.test/api/status/500", "method": "GET"},
                },
            },
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R2",
                    "response": {"status": 500, "mimeType": "application/json"},
                },
            },
            {
                "method": "Network.loadingFailed",
                "params": {"requestId": "R3", "errorText": "net::ERR_ABORTED"},
            },
        ]
    )
    res = net.capture(client, navigation_url, settle=0.2)
    #: le résumé compte l'abandon réseau comme échec et la 500 comme erreur applicative
    assert res["summary"] == {"total": 3, "failed": 1, "errors_4xx_5xx": 1, "bytes": 123}
    #: l'URL de navigation perd credentials, valeur de token et fragment en sortie
    assert res["url"] == "http://s.test/network.html?token=***"
    r1 = next(r for r in res["requests"] if r["requestId"] == "R1")
    #: statut et octets proviennent des trois évènements corrélés par requestId
    assert r1["status"] == 200 and r1["encodedBytes"] == 123
    #: chaque URL de requête est masquée indépendamment de celle de navigation
    assert r1["url"] == "http://s.test/api/json?token=***"
    #: la navigation part avec l'URL brute: le masquage est un artefact de
    #: sortie, pas une altération du comportement du navigateur
    assert mock.commands_for("Page.navigate") == [{"url": navigation_url}]

    # Preuve secondaire: le résumé réseau et les URLs déjà masquées du contrat
    # net.capture, sans credential ni valeur de token.
    if evidence_case is not None:
        evidence_case.attach_json(
            "Résumé net.capture (URLs masquées)",
            {
                "url": res["url"],
                "summary": res["summary"],
                "requests": [
                    {"requestId": r["requestId"], "url": r.get("url"), "status": r.get("status")}
                    for r in res["requests"]
                ],
            },
        )


def test_network_capture_masks_registered_secret_in_url_path(mock, client):
    """Un secret enregistré est masqué même logé dans le chemin d'URL, y
    compris sous sa forme pourcent-encodée."""
    secret = "reset-token-canary"
    navigation_url = f"http://s.test/reset/{secret}"
    mock.script_network(
        [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R1",
                    "type": "Fetch",
                    "request": {
                        "url": "http://s.test/api/reset%2Dtoken%2Dcanary",
                        "method": "GET",
                    },
                },
            }
        ]
    )

    result = net.capture(
        client,
        navigation_url,
        settle=0,
        context=RedactionContext.from_secrets([secret]),
    )

    serialized = json.dumps(result)
    #: la valeur secrète est introuvable dans toute la sortie, même pourcent-encodée
    assert secret not in serialized and "reset%2Dtoken%2Dcanary" not in serialized
    #: les URLs gardent leur structure, seul le segment secret devient le marqueur
    assert result["url"] == "http://s.test/reset/***"
    assert result["requests"][0]["url"] == "http://s.test/api/***"


# -- dev loop ---------------------------------------------------------------------


def _profiler_network_script(base_url: str, headers: dict) -> list[dict]:
    return [
        {
            "method": "Network.responseReceived",
            "params": {
                "requestId": "R1",
                "response": {
                    "url": f"{base_url}/api/profiler-sim",
                    "status": 200,
                    "headers": headers,
                },
            },
        }
    ]


@pytest.mark.scenario(
    feature="dev-profiler-diff",
    journey="read-profiler",
    scenario_id="dev-profiler-diff.read-symfony-profiler",
    proves=["Le profiler expose token masqué et métriques SQL parsées depuis le panel db."],
)
def test_profiler_reads_debug_token_link_and_parses_panels(
    mock, client, fixtures_http, evidence_case
):
    """Le profiler suit X-Debug-Token-Link, récupère le panel db en contexte
    page et en extrait les métriques SQL, sans jamais laisser le token
    apparaître dans la sortie."""
    link = f"{fixtures_http.base_url}/_profiler/fixed-token"
    mock.on_eval("window.location.href", f"{fixtures_http.base_url}/api/profiler-sim")
    mock.script_network(
        _profiler_network_script(fixtures_http.base_url, {"X-Debug-Token-Link": link})
    )
    db_html = (FIXTURES / "profiler" / "db.html").read_text(encoding="utf-8")
    mock.on_eval(
        "__cdpx_profiler_panels",
        json.dumps([{"panel": "db", "status": 200, "html": db_html}]),
    )
    res = dev.profiler(client, f"{fixtures_http.base_url}/api/profiler-sim", panels=["db"])
    #: le token existe mais seule sa présence est annoncée: aucune trace de sa
    #: valeur ni dans l'URL profiler ni ailleurs dans la sortie
    assert "token" not in res and res["token_present"] is True
    assert res["profiler_url"].endswith("/_profiler/***")
    assert "fixed-token" not in json.dumps(res)
    #: le statut rapporté est celui du fetch réel du panel, pas de la page auditée
    assert res["profiler_status"] == 200  # statut réel du fetch du panel
    #: le HTML du panel est réellement parsé: requêtes SQL et doublons comptés
    assert res["panels"]["db"]["queries"] == 6
    assert res["panels"]["db"]["duplicates"] == 4
    #: le contrat de sortie reste minimal, sans champs exploratoires résiduels
    assert "signals" not in res and "profiler_bytes" not in res
    #: l'écoute réseau a été activée pour voir passer les en-têtes de debug
    assert mock.commands_for("Network.enable") == [{}]
    # protocole émis: un seul fetch page-context, promesse attendue
    (call,) = [
        item
        for item in mock.commands_for("Runtime.evaluate")
        if "__cdpx_profiler_panels" in item["expression"]
    ]
    #: le fetch du panel est une promesse attendue, ciblant l'URL du token + panel
    assert call["awaitPromise"] is True
    assert f'"{link}?panel=db"' in call["expression"]

    # Preuve secondaire: la sortie profiler (token masqué, métriques SQL parsées).
    if evidence_case is not None:
        evidence_case.attach_json("Sortie profiler (token masqué, panel db)", res)


def test_profiler_prefers_redirect_response_token(mock, client, fixtures_http):
    """Sur une 302, le token porté par redirectResponse — seul endroit où
    Chrome l'expose — l'emporte sur celui de la page suivie, et le statut
    rapporté est celui de la redirection."""
    # Chrome n'émet pas de responseReceived pour une 302: le token de la
    # redirection n'existe que dans requestWillBeSent.redirectResponse et doit
    # gagner sur celui de la page suivie.
    base = fixtures_http.base_url
    mock.on_eval("window.location.href", f"{base}/scenario/profiler/baseline")
    mock.script_network(
        [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R1",
                    "request": {"url": f"{base}/scenario/profiler/baseline"},
                    "redirectResponse": {
                        "url": f"{base}/scenario/profiler/routing-redirect",
                        "status": 302,
                        "headers": {"X-Debug-Token-Link": f"{base}/_profiler/redir-token"},
                    },
                },
            },
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": f"{base}/scenario/profiler/baseline",
                        "status": 200,
                        "headers": {"X-Debug-Token-Link": f"{base}/_profiler/final-token"},
                    },
                },
            },
        ]
    )
    res = dev.profiler(client, f"{base}/scenario/profiler/routing-redirect", panels=[])
    #: un token a bien été retenu, et ni celui de la redirection ni celui de
    #: la page finale ne fuient en clair dans la sortie
    assert "token" not in res and res["token_present"] is True
    assert res["profiler_url"].endswith("/_profiler/***")
    assert "redir-token" not in json.dumps(res)
    assert "final-token" not in json.dumps(res)
    #: le statut rapporté est celui de la redirection interceptée, pas le 200 final
    assert res["status"] == 302


def test_profiler_falls_back_to_debug_token(mock, client, fixtures_http):
    """Sans X-Debug-Token-Link, l'en-tête X-Debug-Token suffit: l'URL profiler
    est reconstruite et l'en-tête lui-même est masqué dans la sortie."""
    mock.on_eval("window.location.href", f"{fixtures_http.base_url}/api/profiler-sim")
    mock.script_network(
        _profiler_network_script(fixtures_http.base_url, {"X-Debug-Token": "fixed-token"})
    )
    res = dev.profiler(client, f"{fixtures_http.base_url}/api/profiler-sim", panels=[])
    #: présence signalée sans divulguer le token: URL masquée, en-tête masqué,
    #: aucune occurrence de la valeur dans la sortie sérialisée
    assert "token" not in res and res["token_present"] is True
    assert res["profiler_url"].endswith("/_profiler/***")
    assert res["response_headers"]["x-debug-token"] == "***"
    assert "fixed-token" not in json.dumps(res)
    # sonde token seule: aucun fetch de panel
    #: sans panel demandé, aucun fetch page-context n'est même tenté
    assert res["panels"] == {} and res["profiler_status"] is None
    assert not any(
        "__cdpx_profiler_panels" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_profiler_rejects_requested_origin_before_navigation(mock, client):
    """Une origine hors liste blanche est refusée avant d'activer le réseau
    ou de naviguer: le garde-fou opère en amont de tout protocole."""
    #: le refus est une ValueError explicite, pas une navigation avortée en cours
    with pytest.raises(ValueError, match="origine refusée"):
        dev.profiler(
            client,
            "https://attacker.example/report",
            panels=["db"],
            allowed_origins=("http://allowed.test",),
        )

    #: preuve du fail-closed: aucun ordre CDP n'est parti vers le navigateur
    assert mock.commands_for("Network.enable") == []
    assert mock.commands_for("Page.navigate") == []


def test_profiler_rejects_cross_origin_header_before_panel_fetch(mock, client):
    """Un X-Debug-Token-Link pointant vers une origine étrangère est traité
    comme hostile: refus avant tout fetch de panel."""
    url = "http://allowed.test/report"
    mock.on_eval("window.location.href", url)
    mock.script_network(
        _profiler_network_script(
            "http://allowed.test",
            {"X-Debug-Token-Link": "https://attacker.example/_profiler/stolen"},
        )
    )

    #: l'en-tête forgé par le serveur déclenche le refus d'origine
    with pytest.raises(ValueError, match="origine refusée"):
        dev.profiler(client, url, panels=["db"])

    #: aucun fetch de panel n'a été tenté vers l'origine étrangère
    assert not any(
        "__cdpx_profiler_panels" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_profiler_rejects_forbidden_final_url_before_panel_fetch(mock, client):
    """Même quand l'URL demandée est autorisée, une redirection vers une
    origine interdite bloque le profiler avant le fetch de panel: la
    destination réelle fait foi."""
    requested = "http://allowed.test/report"
    mock.on_eval("window.location.href", "https://attacker.example/redirected")
    mock.script_network(
        _profiler_network_script(
            "http://allowed.test",
            {"X-Debug-Token-Link": "http://allowed.test/_profiler/token"},
        )
    )

    #: c'est l'URL finale après redirection qui est jugée, pas celle demandée
    with pytest.raises(ValueError, match="origine refusée"):
        dev.profiler(
            client,
            requested,
            panels=["db"],
            allowed_origins=("http://allowed.test",),
        )

    #: aucun panel n'a été récupéré depuis la page détournée
    assert not any(
        "__cdpx_profiler_panels" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_dom_diff_runs_action_and_returns_unified_diff(mock, client):
    """dom-diff exécute réellement l'action entre deux snapshots et rend un
    diff qui localise la mutation du DOM."""
    before = ["<body>", '  <div#result[data-state="idle"]>']
    after = ["<body>", '  <div#result[data-state="submitted"]>', '    "OK:Léo"']
    mock.on_eval("__cdpx_dom_snapshot", json.dumps(before), json.dumps(after))
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    res = dev.dom_diff(client, ["click", "#submit-btn"])
    #: le diff détecte la mutation et en expose la ligne changée, exploitable telle quelle
    assert res["changed"] is True
    assert any("submitted" in line for line in res["diff"])
    #: l'action n'a pas été simulée: la séquence souris complète est bien partie
    assert [m["type"] for m in mock.commands_for("Input.dispatchMouseEvent")] == [
        "mouseMoved",
        "mousePressed",
        "mouseReleased",
    ]


def test_dom_diff_is_stable_across_runs_on_same_state(mock, client):
    """Deux exécutions de dom-diff sur un état DOM identique produisent un
    diff strictement identique: la sortie est déterministe, pas dépendante
    du run."""
    before = ["<body>", '  <div#result[data-state="idle"]>']
    after = ["<body>", '  <div#result[data-state="submitted"]>', '    "OK:Léo"']
    #: le mock rejoue exactement le même couple avant/après pour chaque run
    mock.on_eval(
        "__cdpx_dom_snapshot",
        json.dumps(before),
        json.dumps(after),
        json.dumps(before),
        json.dumps(after),
    )
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    first = dev.dom_diff(client, ["click", "#submit-btn"])
    second = dev.dom_diff(client, ["click", "#submit-btn"])

    #: le diff est strictement identique d'un run à l'autre, ligne à ligne
    assert first["diff"] == second["diff"]
    #: la stabilité couvre toute la sortie, pas seulement le champ diff
    assert first == second
    #: garde-fou: le diff comparé n'est pas trivialement vide
    assert first["changed"] is True
    assert first["lines"] > 0


# -- state ------------------------------------------------------------------------


def test_cookies_masked_by_default(mock, client):
    """Les valeurs de cookies sont masquées par défaut; les lire en clair
    exige l'opt-in explicite show_values."""
    res = state.get_cookies(client)
    #: par défaut la sortie s'auto-déclare masquée et ne montre que le marqueur
    assert res["values_masked"] is True
    assert res["cookies"][0]["value"] == "***"
    res2 = state.get_cookies(client, show_values=True)
    #: l'opt-in révèle la vraie valeur: le masquage n'est pas destructif
    assert res2["cookies"][0]["value"] == "secret-session-token"


def test_set_and_clear_cookies(mock, client):
    """set_cookie écrit réellement dans le jar du navigateur et clear_cookies
    le vide via l'API Storage moderne."""
    state.set_cookie(client, "flag", "1", "http://127.0.0.1/")
    #: le cookie posé est visible côté navigateur, pas seulement accepté par l'API
    assert any(c["name"] == "flag" for c in mock.cookies)
    res = state.clear_cookies(client)
    #: la purge annonce la méthode employée et le jar est effectivement vide
    assert res["method"] == "Storage.clearCookies"
    assert mock.cookies == []


def test_clear_cookies_falls_back_on_legacy_method(mock, client):
    """Quand Storage.clearCookies échoue (Chrome ancien), le repli sur la
    méthode dépréciée aboutit et la sortie annonce la méthode réellement
    utilisée."""
    # Chrome historique sans Storage.clearCookies: repli sur la méthode dépréciée.
    mock.fail_on("Storage.clearCookies")
    res = state.clear_cookies(client)
    #: la sortie dit la vérité: c'est la méthode de repli qui a nettoyé
    assert res == {"cleared": True, "method": "Network.clearBrowserCookies"}
    #: l'ordre prouve la tentative moderne d'abord, le repli ensuite seulement
    assert [m for (_t, m, _p) in mock.commands] == [
        "Storage.clearCookies",
        "Network.clearBrowserCookies",
    ]
    #: le repli a réellement vidé le jar, pas seulement rendu un statut
    assert mock.cookies == []


def test_get_storage_masks_values_by_default_with_explicit_opt_in(mock, client):
    """Le storage est masqué par défaut (valeurs remplacées, drapeau
    values_masked levé) et seul show_values=True livre les données réelles."""
    secret = "storage-secret-value"
    mock.on_eval("localStorage", json.dumps({"cdpx-key": secret}))
    res = state.get_storage(client, "local")
    shown = state.get_storage(client, "local", show_values=True)

    #: la lecture par défaut compte les entrées mais remplace chaque valeur,
    #: et la valeur secrète est absente de toute la sortie sérialisée
    assert res == {
        "kind": "local",
        "entries": {"cdpx-key": "***"},
        "count": 1,
        "values_masked": True,
    }
    assert secret not in json.dumps(res)
    #: l'opt-in retourne les mêmes clés avec les valeurs en clair, drapeau baissé
    assert shown == {
        "kind": "local",
        "entries": {"cdpx-key": secret},
        "count": 1,
        "values_masked": False,
    }
    #: deux lectures = deux évaluations distinctes, aucun cache implicite
    assert len(mock.commands_for("Runtime.evaluate")) == 2


# -- audit ------------------------------------------------------------------------

SEO_OK = {
    "url": "http://127.0.0.1/seo.html",
    "lang": "fr",
    "title": "Fixture SEO — page conforme",
    "metas": {"description": "ok", "robots": "index,follow"},
    "canonical": "http://127.0.0.1/seo.html",
    "robots": "index,follow",
    "h1": ["Unique H1 conforme"],
    "hreflang": [{"lang": "fr", "href": "/seo.html"}, {"lang": "en", "href": "/en/seo.html"}],
    "jsonld": [{"@type": "Product", "sku": "FIX-001"}],
    "images_without_alt": 0,
    "links": {"internal": 1, "external": 1, "nofollow": 1},
}

SEO_BROKEN = {
    "url": "http://127.0.0.1/seo-broken.html",
    "lang": None,
    "title": "",
    "metas": {},
    "canonical": None,
    "robots": None,
    "h1": ["Premier H1", "Deuxième H1 (erreur)"],
    "hreflang": [],
    "jsonld": [],
    "images_without_alt": 2,
    "links": {"internal": 0, "external": 0, "nofollow": 0},
}


def test_seo_clean_page_no_findings(mock, client):
    """Une page SEO conforme ne produit aucun finding — zéro faux positif —
    tout en livrant les données informatives (largeur du title, JSON-LD)."""
    mock.on_eval("__cdpx_seo", json.dumps(SEO_OK))
    res = audit.seo(client)
    #: aucun faux positif sur la page témoin conforme
    assert res["findings"] == []
    #: les métriques informatives restent fournies même sans problème détecté
    assert res["title_px_estimate"] > 0
    assert res["jsonld"][0]["@type"] == "Product"


def test_seo_broken_page_findings(mock, client):
    """Chaque défaut SEO majeur de la page cassée produit son finding nommé:
    title, description, canonical, h1 multiples et images sans alt."""
    mock.on_eval("__cdpx_seo", json.dumps(SEO_BROKEN))
    res = audit.seo(client)
    #: chaque manquement est libellé en clair, directement exploitable en CLI
    assert "title manquant" in res["findings"]
    assert "meta description manquante" in res["findings"]
    assert "canonical manquant" in res["findings"]
    assert "2 h1 (attendu: 1)" in res["findings"]
    assert "2 image(s) sans alt" in res["findings"]


def test_seo_advanced_findings(mock, client):
    """L'audit détecte les cas subtils: h1 dupliqués (comparaison en casse
    pliée), JSON-LD malformé et Product incomplet."""
    payload = {
        **SEO_OK,
        "h1": ["Same", "Same"],
        "jsonld": [{"@type": "Product"}, {"__parse_error": "SyntaxError"}],
        "images_without_alt": 1,
    }
    mock.on_eval("__cdpx_seo", json.dumps(payload))
    res = audit.seo(client)
    #: les doublons sont repérés indépendamment de la casse, et le JSON-LD est
    #: jugé sur sa validité syntaxique ET sa complétude métier
    assert "h1 dupliqué: same" in res["findings"]
    assert "JSON-LD invalide" in res["findings"]
    assert "Product JSON-LD incomplet (sku ou name requis)" in res["findings"]


def test_seo_accepts_top_level_jsonld_arrays_and_reports_scalars(mock, client):
    """Un script JSON-LD contenant un tableau de premier niveau est déplié et
    audité objet par objet; un scalaire est signalé sans faire planter
    l'audit."""
    payload = {
        **SEO_OK,
        "jsonld": [
            [{"@type": "Product", "name": "Valid"}, {"@type": "Product"}],
            "not-an-object",
        ],
    }
    mock.on_eval("__cdpx_seo", json.dumps(payload))
    res = audit.seo(client)
    #: l'objet incomplet niché dans le tableau est trouvé, et le scalaire
    #: produit un finding au lieu d'une exception
    assert res["findings"] == [
        "Product JSON-LD incomplet (sku ou name requis)",
        "JSON-LD scalaire non supporté",
    ]


def test_metrics(mock, client):
    """Les métriques du domaine Performance sont remises à plat sous leur nom
    d'origine, valeurs numériques intactes."""
    res = audit.metrics(client)
    #: les compteurs du protocole traversent sans conversion ni renommage
    assert res["Nodes"] == 42 and res["JSHeapUsedSize"] == 1048576


# -- advanced ------------------------------------------------------------------


def test_intercept_goto_fulfills_matching_request(mock, client):
    """Une règle '=> 503' satisfait artificiellement la requête interceptée
    avec ce statut et journalise le hit correspondant."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {
                    "requestId": "I1",
                    "request": {"url": "http://s.test/api/payment", "method": "POST"},
                },
            }
        ]
    )
    res = advanced.intercept_goto(client, ["*payment* => 503"], "http://s.test/checkout")
    #: le hit journalisé relie l'URL interceptée à l'action de la règle appliquée
    assert res["hits"] == [{"url": "http://s.test/api/payment", "action": "503"}]
    #: la requête a été satisfaite côté protocole avec le statut simulé
    assert mock.commands_for("Fetch.fulfillRequest")[0]["responseCode"] == 503


def test_intercept_goto_blocks_and_continues(mock, client):
    """Chaque requête interceptée reçoit sa propre décision: la règle bloque
    A tandis que B, sans règle, continue par défaut."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {"requestId": "A", "request": {"url": "http://s.test/a"}},
            },
            {
                "method": "Fetch.requestPaused",
                "params": {"requestId": "B", "request": {"url": "http://s.test/b"}},
            },
        ]
    )
    res = advanced.intercept_goto(client, ["*a => block"], "http://s.test/")
    #: le journal distingue le blocage ciblé de la continuation par défaut
    assert res["hits"] == [
        {"url": "http://s.test/a", "action": "block"},
        {"url": "http://s.test/b", "action": "continue"},
    ]
    #: côté protocole, A échoue et B poursuit — les deux décisions sont explicites
    assert mock.commands_for("Fetch.failRequest")[0]["requestId"] == "A"
    assert mock.commands_for("Fetch.continueRequest")[0]["requestId"] == "B"


@pytest.mark.parametrize(
    "rule",
    [
        "broken",
        "=> block",
        "* =>",
        "* => typo",
        "* => Continue",
        "* => 199",
        "* => 600",
        "* => 200.0",
    ],
)
def test_intercept_rejects_invalid_rule_before_cdp(mock, client, rule):
    """Toute règle syntaxiquement invalide (pattern absent, action inconnue,
    statut hors bornes ou non entier) est rejetée avant le moindre échange
    CDP."""
    #: la grammaire des règles est validée à froid, quel que soit le défaut du cas
    with pytest.raises(ValueError):
        advanced.intercept_goto(client, [rule], "http://s.test/")
    #: fail-closed: le navigateur n'a rien vu passer
    assert mock.commands == []


def test_intercept_prevalidates_every_rule_before_cdp(mock, client):
    """Une règle invalide en deuxième position condamne tout le lot: chaque
    règle est validée avant la première commande CDP."""
    #: la règle valide en tête ne suffit pas à démarrer l'interception
    with pytest.raises(ValueError):
        advanced.intercept_goto(
            client,
            ["*first* => continue", "*second* => typo"],
            "http://s.test/",
        )
    #: aucune commande partielle: c'est tout ou rien
    assert mock.commands == []


@pytest.mark.parametrize("status", [200, 599])
def test_intercept_accepts_status_bounds(mock, client, status):
    """Les statuts aux bornes du domaine accepté (200 et 599) passent la
    validation et sont réellement appliqués à la requête interceptée."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {
                    "requestId": "I1",
                    "request": {"url": "http://s.test/api/status"},
                },
            }
        ]
    )
    res = advanced.intercept_goto(
        client,
        [f"*status* => {status}"],
        "http://s.test/",
        settle=0,
    )
    #: le statut limite traverse jusqu'au fulfillRequest du protocole, sans arrondi
    assert res["hits"] == [{"url": "http://s.test/api/status", "action": str(status)}]
    assert mock.commands_for("Fetch.fulfillRequest")[0]["responseCode"] == status


def test_intercept_accepts_explicit_continue(mock, client):
    """L'action 'continue' explicite laisse passer la requête interceptée
    tout en la journalisant comme hit."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {
                    "requestId": "I1",
                    "request": {"url": "http://s.test/api/continue"},
                },
            }
        ]
    )
    res = advanced.intercept_goto(
        client,
        ["*continue* => continue"],
        "http://s.test/",
        settle=0,
    )
    #: le hit est journalisé même quand la règle laisse passer la requête
    assert res["hits"] == [{"url": "http://s.test/api/continue", "action": "continue"}]
    #: la requête poursuit son chemin via continueRequest, sans altération
    assert mock.commands_for("Fetch.continueRequest") == [{"requestId": "I1"}]


def test_emulate_mobile_and_reset(mock, client):
    """L'émulation mobile applique l'override device, et le reset restaure
    tout — UA comprise (bug historique) — via la séquence protocolaire
    complète."""
    #: le profil mobile est appliqué avec le drapeau device correspondant
    assert advanced.emulate(client, "mobile")["applied"] is True
    assert mock.commands_for("Emulation.setDeviceMetricsOverride")[0]["mobile"] is True
    mock.commands.clear()
    assert advanced.emulate(client, reset=True)["reset"] is True
    # Séquence complète de reset, UA compris (bug historique: UA mobile jamais
    # restaurée; vérifié contre Chrome réel — setUserAgentOverride "" rétablit
    # l'UA par défaut, clearDeviceMetricsOverride lève l'override device).
    #: la séquence de reset couvre device, UA, réseau et CPU — rien n'est oublié
    assert [m for (_t, m, _p) in mock.commands] == [
        "Emulation.clearDeviceMetricsOverride",
        "Emulation.setUserAgentOverride",
        "Network.emulateNetworkConditions",
        "Emulation.setCPUThrottlingRate",
    ]
    #: UA vide = retour à l'UA par défaut de Chrome; rate 1 = CPU non bridé
    assert mock.commands_for("Emulation.setUserAgentOverride")[0] == {"userAgent": ""}
    assert mock.commands_for("Emulation.setCPUThrottlingRate")[0] == {"rate": 1}


def test_vitals_installs_observer_and_reads_values(mock, client):
    """vitals installe l'observer avant la navigation, déclenche l'interaction
    demandée puis lit les métriques web vitals depuis la page."""
    mock.on_eval("__cdpxVitals", json.dumps({"lcp": 12, "cls": 0.1, "inp": 0}))
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    res = advanced.vitals(client, "http://s.test/vitals.html", click_selector="#inp-button")
    #: les métriques rapportées proviennent bien de l'observer injecté dans la page
    assert res["lcp"] == 12 and res["cls"] == 0.1
    #: l'observer était en place avant le chargement, et le clic INP a eu lieu
    assert mock.commands_for("Page.addScriptToEvaluateOnNewDocument")
    assert mock.commands_for("Input.dispatchMouseEvent")


def test_vitals_rechecks_redirected_origin_before_click(mock, client):
    """Même après une navigation autorisée, une redirection hors origines
    permises bloque le clic INP: la mutation est re-jugée sur l'URL réelle."""
    mock.on_eval("window.location.href", "https://prod.example/redirected")
    #: la mutation est refusée sur la destination réelle, pas sur l'URL demandée
    with pytest.raises(ValueError, match="mutation refusée"):
        advanced.vitals(
            client,
            "http://allowed.test/vitals.html",
            click_selector="#go",
            origins="http://*.test",
        )
    #: aucun clic n'a été émis vers la page détournée
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_a11y_compacts_ax_tree(mock, client):
    """L'arbre d'accessibilité est compacté en une liste plate de noeuds qui
    conservent leur rôle exploitable pour l'audit."""
    res = advanced.a11y(client)
    #: la compaction préserve le compte des noeuds et leurs rôles significatifs
    assert res["count"] == 2
    assert res["nodes"][1]["role"] == "button"


def test_coverage_aggregates_files(mock, client):
    """La couverture agrège JS et CSS par fichier sous un contrat stable; sans
    donnée mesurable, le pourcentage vaut None plutôt qu'un faux zéro."""
    res = advanced.coverage(client, "http://s.test/")
    #: le contrat par fichier est complet et l'indéterminé reste None, pas 0
    assert res["files"][0] == {
        "url": "http://fixture/app.js",
        "functions": 1,
        "used_ranges": 0,
        "total_bytes": 0,
        "used_bytes": 0,
        "unused_bytes": 0,
        "coverage_percent": None,
    }
    #: les agrégats JS et CSS séparent l'utilisé de l'inutilisé
    assert res["js"] == {"total_bytes": 0, "used_bytes": 0, "unused_bytes": 0}
    assert res["css"] == {"rules": 2, "used": 1, "unused": 1}


def test_coverage_reports_byte_coverage_not_range_counts(mock, client, monkeypatch):
    """La couverture JS se mesure en octets réellement exécutés — plages
    mortes soustraites, chevauchements déduits — et non en nombre de
    plages."""
    original_send = client.send

    def send(method, params=None, timeout=None):
        if method == "Profiler.takePreciseCoverage":
            return {
                "result": [
                    {
                        "url": "http://fixture/app.js",
                        "functions": [
                            {"ranges": [{"startOffset": 0, "endOffset": 100, "count": 1}]},
                            {"ranges": [{"startOffset": 20, "endOffset": 40, "count": 0}]},
                        ],
                    }
                ]
            }
        return original_send(method, params, timeout)

    monkeypatch.setattr(client, "send", send)
    res = advanced.coverage(client, "http://s.test/")
    #: 100 octets dont 20 morts donnent 80%: le calcul soustrait les plages
    #: non exécutées au lieu de compter les plages
    assert res["js"] == {"total_bytes": 100, "used_bytes": 80, "unused_bytes": 20}
    assert res["files"][0]["coverage_percent"] == 80.0


def test_frame_text_reads_iframe_content(mock, client):
    """frame_text lit le texte à l'intérieur d'un iframe via son
    contentDocument, hors de portée d'un querySelector de la page hôte."""
    mock.on_eval("contentDocument", "iframe text")
    #: le texte retourné vient du document embarqué, pas de la page hôte
    assert advanced.frame_text(client, "#child-marker")["text"] == "iframe text"


def test_record_executes_action_and_journals_result(mock, client, tmp_path):
    """record exécute réellement l'action (protocole émis) et journalise
    l'évènement NDJSON complet: action, issue et résultat structuré."""
    path = tmp_path / "record.ndjson"
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    res = advanced.record(client, str(path), ["click", "#submit"], origins="http://*.test")
    #: le bilan annonce un succès et exactement un évènement journalisé
    assert res["ok"] is True and res["recorded"] == 1
    # l'action a été réellement exécutée (protocole émis), pas seulement journalisée
    #: preuve d'exécution réelle: la séquence souris complète est partie vers la page
    assert [m["type"] for m in mock.commands_for("Input.dispatchMouseEvent")] == [
        "mouseMoved",
        "mousePressed",
        "mouseReleased",
    ]
    event = json.loads(path.read_text().splitlines()[0])
    #: le journal NDJSON conserve l'action, son issue et son résultat, base du rejeu
    assert event["action"] == ["click", "#submit"]
    assert event["ok"] is True
    assert event["result"]["clicked"] == "#submit"


def test_record_journals_failure_then_raises(mock, client, tmp_path):
    """Un échec d'action est d'abord journalisé (ok=false + erreur) puis
    l'exception remonte au CLI: le journal ne perd jamais l'échec."""
    path = tmp_path / "record.ndjson"
    mock.on_eval("getBoundingClientRect", None)  # élément introuvable
    #: l'échec remonte comme exception typée, après écriture du journal
    with pytest.raises(inputs.ElementNotFound):
        advanced.record(client, str(path), ["click", "#missing"], origins="http://*.test")
    event = json.loads(path.read_text().splitlines()[0])
    #: la trace conserve l'action tentée, son échec et le message d'erreur
    assert event["ok"] is False and event["action"] == ["click", "#missing"]
    assert "#missing" in event["result"]["error"]


def test_replay_reexecutes_journal_against_browser(mock, client, tmp_path):
    """replay ré-exécute chaque évènement du journal contre le navigateur,
    dans l'ordre d'enregistrement, et rapporte un rejeu intégral fidèle."""
    path = tmp_path / "record.ndjson"
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    advanced.record(client, str(path), ["goto", "http://site.test/"], origins="http://*.test")
    advanced.record(client, str(path), ["click", "#submit"], origins="http://*.test")
    mock.commands.clear()
    res = advanced.replay(client, str(path), origins="http://*.test")
    #: le bilan atteste deux évènements lus, deux joués, aucune divergence
    assert res == {"path": str(path), "events": 2, "played": 2, "ok": True}
    # le rejeu a bien ré-émis navigation puis clic, dans l'ordre du journal
    methods = [m for (_t, m, _p) in mock.commands]
    #: l'ordre du protocole ré-émis respecte l'ordre du journal
    assert methods.index("Page.navigate") < methods.index("Input.dispatchMouseEvent")


def test_replay_rejects_v1_type_without_exposing_text(client, tmp_path, monkeypatch):
    """Un journal v1 contenant un texte tapé en clair est refusé sans être
    rejoué et sans que la valeur sensible héritée ne fuie dans le bilan."""
    path = tmp_path / "legacy-type.ndjson"
    path.write_text(
        '{"action":["type","#name","legacy-secret"],"ok":true,'
        '"result":{"typed":"legacy-secret","selector":"#name","cleared":false}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        advanced.actions,
        "run_action",
        lambda _client, _action: {
            "typed": True,
            "value_masked": True,
            "selector": "#name",
            "cleared": False,
        },
    )

    result = advanced.replay(client, str(path), origins="http://*.test")

    #: refus net: aucune action jouée sur un format d'archive sensible
    assert result["ok"] is False and result["played"] == 0
    #: le refus est motivé et la valeur secrète héritée n'apparaît nulle part
    assert "v1 sensible" in result["divergence"]
    assert "legacy-secret" not in json.dumps(result)


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="replay-flow",
    scenario_id="orchestration-control.orchestrate-replay-and-emulation",
    proves=["Le rejeu s'arrête net à la première divergence, sans rejouer la suite."],
)
def test_replay_stops_at_first_divergence(mock, client, tmp_path, evidence_case):
    """Le rejeu s'arrête net à la première divergence: l'évènement fautif est
    identifié et les évènements suivants ne sont jamais exécutés."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://site.test/"],"ok":true}\n'
        '{"action":["click","#gone"],"ok":true}\n'
        '{"action":["goto","http://after.test/"],"ok":true}\n',
        encoding="utf-8",
    )
    mock.on_eval("getBoundingClientRect", None)  # le clic rejoué échoue
    res = advanced.replay(client, str(path), origins="http://*.test")
    #: une seule action jouée avant l'échec, divergence localisée sur le fautif
    assert res["ok"] is False and res["played"] == 1
    assert res["divergence"].startswith("event 1:")
    # arrêt net: l'action suivante du journal n'a pas été rejouée
    #: la navigation qui suivait le clic fautif n'a jamais été ré-émise
    assert [p.get("url") for p in mock.commands_for("Page.navigate")] == ["http://site.test/"]

    # Preuve secondaire: le journal NDJSON rejoué (typé logs) et l'objet
    # divergence qui documente l'arrêt net au premier écart.
    if evidence_case is not None:
        evidence_case.attach_file(path, "Journal record.ndjson rejoué")
        evidence_case.attach_json(
            "Divergence du rejeu (arrêt net)",
            {"ok": res["ok"], "played": res["played"], "divergence": res["divergence"]},
        )


def test_replay_divergence_on_journaled_failure(mock, client, tmp_path):
    """Un évènement journalisé en échec est une divergence immédiate: il ne
    se rejoue pas et rien ne part vers le navigateur."""
    path = tmp_path / "record.ndjson"
    path.write_text('{"action":["click","#submit"],"ok":false}\n', encoding="utf-8")
    res = advanced.replay(client, str(path), origins="http://*.test")
    #: la divergence cite l'échec journalisé sans avoir tenté de le reproduire
    assert res["ok"] is False and res["divergence"] == "event 0: ok=false journalisé"
    #: fail-closed: aucune commande CDP n'a été émise
    assert mock.commands == []  # un enregistrement en échec ne se rejoue pas


def test_replay_validates_journal_before_any_execution(mock, client, tmp_path):
    """Le journal entier est validé (JSON, action présente, budget
    max_actions) avant la moindre exécution: toute corruption bloque tout."""
    path = tmp_path / "record.ndjson"
    path.write_text('{"action":["goto","http://x.test/"],"ok":true}\n{not-json}\n', "utf-8")
    res = advanced.replay(client, str(path), origins="http://*.test")
    #: la ligne corrompue est localisée et même la première ligne valide n'est
    #: pas rejouée
    assert res["ok"] is False and res["divergence"].startswith("line 2:")
    assert mock.commands == []  # journal corrompu -> rien n'est rejoué
    path.write_text('{"ok":true}\n', encoding="utf-8")
    #: une entrée sans action est un journal invalide, signalé par sa ligne
    assert (
        advanced.replay(client, str(path), origins="http://*.test")["divergence"]
        == "line 1: action manquante"
    )
    path.write_text('{"action":["goto","http://x.test/"],"ok":true}\n' * 3, encoding="utf-8")
    #: dépasser le budget d'actions lève avant toute émission protocolaire
    with pytest.raises(ValueError):
        advanced.replay(client, str(path), max_actions=2, origins="http://*.test")
    assert mock.commands == []  # budget dépassé -> rien n'est rejoué


def test_replay_validates_action_grammar_before_any_execution(mock, client, tmp_path):
    """Un verbe hors grammaire dans le journal est refusé à la validation,
    avant que la moindre action — même valide — ne soit rejouée."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://x.test/"],"ok":true,"result":{"ok":true}}\n'
        '{"action":["shell","oops"],"ok":true,"result":{}}\n',
        encoding="utf-8",
    )
    res = advanced.replay(client, str(path), origins="http://*.test")
    #: le verbe interdit est repéré à sa ligne et rien n'a été exécuté, pas
    #: même la première action pourtant valide
    assert res["ok"] is False and res["divergence"].startswith("line 2:")
    assert mock.commands == []


def test_replay_compares_semantic_results(mock, client, tmp_path):
    """Le rejeu compare les résultats sémantiquement: un champ significatif
    divergent est signalé avec chemin/attendu/obtenu, tandis que les champs
    volatils comme elapsed_ms sont ignorés."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://x.test/"],"ok":true,'
        '"result":{"url":"http://other.test/","ok":true,"elapsed_ms":999}}\n',
        encoding="utf-8",
    )
    res = advanced.replay(client, str(path), origins="http://*.test")
    #: la divergence structurée ne cite que le champ significatif (url), pas
    #: le chronométrage volatil pourtant différent
    assert res["ok"] is False and res["played"] == 1
    assert res["divergence"] == {
        "event": 0,
        "kind": "result_mismatch",
        "differences": [
            {"path": "$.url", "expected": "http://other.test/", "actual": "http://x.test/"}
        ],
    }


def test_replay_origin_guard_follows_goto_before_mutation(mock, client, tmp_path):
    """Le garde d'origine suit la navigation du journal: un goto vers une
    origine interdite bloque le rejeu avant la mutation qui suit."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://prod.example/"],"ok":true}\n'
        '{"action":["click","#submit"],"ok":true}\n',
        encoding="utf-8",
    )
    mock.on_eval("window.location.href", "http://prod.example/")
    res = advanced.replay(client, str(path), origins="http://*.test")
    #: la navigation interdite bloque le rejeu avant même la première action
    assert res["ok"] is False and res["played"] == 0
    #: le refus est motivé par l'origine et le clic n'est jamais parti
    assert "origine refusée" in str(res["divergence"])
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_replay_origin_guard_uses_redirect_destination_before_mutation(mock, client, tmp_path):
    """C'est la destination réelle après redirection qui est jugée: un goto
    autorisé aboutissant hors zone bloque la mutation suivante dès la
    première lecture d'URL."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","http://allowed.test/start"],"ok":true}\n'
        '{"action":["click","#submit"],"ok":true}\n',
        encoding="utf-8",
    )
    mock.on_eval("window.location.href", "https://prod.example/redirected")

    res = advanced.replay(client, str(path), origins="http://*.test")

    #: le goto s'est joué mais la mutation qui suit est refusée
    assert res["ok"] is False and res["played"] == 1
    #: origine refusée: la souris reste muette face à la page détournée
    assert "origine refusée" in str(res["divergence"])
    assert mock.commands_for("Input.dispatchMouseEvent") == []
    location_reads = [
        params
        for params in mock.commands_for("Runtime.evaluate")
        if params["expression"] == "window.location.href"
    ]
    #: une seule lecture d'URL a suffi: le refus est immédiat, jamais retenté
    assert len(location_reads) == 1  # destination réelle refusée immédiatement après goto


def test_replay_rejects_forbidden_goto_before_navigation(mock, client, tmp_path):
    """Un goto du journal vers une origine interdite est refusé avant de
    naviguer: le garde couvre aussi les actions qui déplacent le contexte."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"action":["goto","https://forbidden.example/"],"ok":true}\n',
        encoding="utf-8",
    )

    result = advanced.replay(
        client,
        str(path),
        origins="http://allowed.test",
    )

    #: rejeu refusé à zéro action jouée, motivé par l'origine interdite
    assert result["ok"] is False and result["played"] == 0
    assert "origine refusée" in result["divergence"]
    #: la navigation interdite n'a jamais été émise vers le navigateur
    assert mock.commands_for("Page.navigate") == []


def test_record_rejects_forbidden_goto_before_navigation_or_journal(mock, client, tmp_path):
    """record refuse un goto hors origines avant de naviguer ET avant d'ouvrir
    le journal: une action interdite ne laisse aucun artefact."""
    path = tmp_path / "record.ndjson"

    #: l'interdiction se joue en amont de tout effet de bord
    with pytest.raises(ValueError, match="origine refusée"):
        advanced.record(
            client,
            str(path),
            ["goto", "https://forbidden.example/"],
            origins="http://allowed.test",
        )

    #: ni navigation émise, ni fichier journal créé sur disque
    assert mock.commands_for("Page.navigate") == []
    assert not path.exists()


@pytest.mark.parametrize(
    ("events", "played"),
    [
        ('{"action":["click","#submit"],"ok":true}\n', 0),
        (
            '{"action":["goto","http://allowed.test/start"],"ok":true}\n'
            '{"action":["click","#submit"],"ok":true}\n',
            1,
        ),
    ],
)
def test_replay_origin_guard_fails_closed_when_current_url_is_unknown(
    mock, client, tmp_path, events, played
):
    """Quand l'URL courante est indéterminable, le garde échoue fermé: la
    mutation est refusée, que le journal commence ou non par un goto
    autorisé."""
    path = tmp_path / "record.ndjson"
    path.write_text(events, encoding="utf-8")
    mock.on_eval("window.location.href", None)

    res = advanced.replay(client, str(path), origins="http://*.test")

    #: le rejeu s'arrête exactement là où l'URL devient nécessaire au jugement
    assert res["ok"] is False and res["played"] == played
    #: le motif est l'indétermination, et aucun clic n'est parti à l'aveugle
    assert "URL courante indéterminable" in str(res["divergence"])
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_replay_origin_guard_is_kept_after_mutation(mock, client, tmp_path):
    """Le garde reste actif après chaque mutation: une redirection post-clic
    hors zone autorisée est détectée et signalée comme divergence."""
    path = tmp_path / "record.ndjson"
    path.write_text('{"action":["click","#submit"],"ok":true}\n', encoding="utf-8")
    mock.on_eval(
        "window.location.href",
        "http://allowed.test/form",
        "https://prod.example/redirected",
    )
    mock.on_eval(
        "getBoundingClientRect",
        json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}),
    )

    res = advanced.replay(client, str(path), origins="http://*.test")

    #: le clic s'est joué puis la destination résultante a été refusée
    assert res["ok"] is False and res["played"] == 1
    assert "destination après action: origine refusée" in str(res["divergence"])
    #: la mutation avait bien eu lieu (séquence souris complète) avant détection
    assert len(mock.commands_for("Input.dispatchMouseEvent")) == 3


def test_origin_guard_blocks_mutations_only_when_configured():
    """Le garde distingue lecture et mutation: hors zone autorisée, lire est
    permis mais cliquer est refusé; en zone, tout passe."""
    #: lire hors zone est permis: le garde ne bride pas l'observation
    advanced.assert_origin_allowed("text", "https://prod.example/", "http://*.test")
    #: la même origine devient interdite dès que l'action mute la page
    with pytest.raises(ValueError):
        advanced.assert_origin_allowed("click", "https://prod.example/", "http://*.test")
    #: en zone autorisée, la mutation passe sans lever
    advanced.assert_origin_allowed("click", "http://shop.test/page", "http://*.test")


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="replay-flow",
    scenario_id="orchestration-control.orchestrate-replay-and-emulation",
    proves=["La garde CDPX_ORIGINS classe chaque commande selon sa mutation effective."],
)
def test_origin_guard_classifies_commands_by_effective_mutation():
    """La classification mutation/lecture suit l'effet réel: verbes simples
    classés en dur, commandes composées jugées sur le verbe de l'action
    qu'elles embarquent."""
    # Contrat de sécurité: mutations refusées hors CDPX_ORIGINS, lectures permises.
    # Pour les commandes composées, c'est le VERBE de l'action qui décide.
    mutates = advanced.command_mutates
    #: tous les verbes qui écrivent dans la page sont classés mutation, replay
    #: compris puisque son journal peut contenir n'importe quelle action
    assert all(mutates(c) for c in ("click", "type", "key", "eval", "intercept"))
    assert mutates("replay")  # le journal rejoué peut contenir n'importe quelle action
    #: les lectures pures ne déclenchent jamais le garde
    assert not mutates("text") and not mutates("goto") and not mutates("seo")
    #: une commande composée hérite de la nature de son action embarquée
    for composed in ("dom-diff", "record", "emulate"):
        assert mutates(composed, ["click", "#x"])
        assert mutates(composed, ["eval", "1"])
        assert not mutates(composed, ["goto", "http://x.test/"])
        assert not mutates(composed, [])
    #: emulate sans action embarquée est une neutralisation, pas une mutation
    assert not mutates("emulate", None)  # emulate --reset seul: lecture/neutralisation
    #: vitals et cookies ne mutent que si leur sous-action écrit réellement
    assert mutates("vitals", ["click", "#button"])
    assert not mutates("vitals", [])
    assert mutates("cookies", ["set"])
    assert mutates("cookies", ["clear"])
    assert not mutates("cookies", ["get"])


def test_origin_guard_checks_composed_action_verb():
    """Pour une commande composée, c'est le verbe de l'action embarquée qui
    déclenche le refus; replay hors zone est toujours refusé."""
    #: dom-diff hors zone est refusé quand son action embarquée clique
    with pytest.raises(ValueError):
        advanced.assert_origin_allowed(
            "dom-diff", "https://prod.example/", "http://*.test", action=["click", "#x"]
        )
    #: le même dom-diff passe quand l'action embarquée ne fait que naviguer
    advanced.assert_origin_allowed(
        "dom-diff", "https://prod.example/", "http://*.test", action=["goto", "http://a.test/"]
    )
    #: replay est toujours traité en mutation: son journal peut tout contenir
    with pytest.raises(ValueError):
        advanced.assert_origin_allowed("replay", "https://prod.example/", "http://*.test")


def test_run_action_dispatches_and_rejects_unknown(mock, client):
    """run_action route chaque verbe connu vers sa primitive — preuve
    protocolaire à l'appui — et rejette tout verbe hors grammaire, action
    vide comprise."""
    from cdpx.primitives import actions

    res = actions.run_action(client, ["goto", "http://site.test/"])
    #: le verbe goto atteint la vraie primitive de navigation, protocole émis
    assert res["ok"] is True
    assert mock.commands_for("Page.navigate") == [{"url": "http://site.test/"}]
    mock.on_eval("2 + 2", 4)
    #: le verbe eval retourne la valeur calculée par la page
    assert actions.run_action(client, ["eval", "2 + 2"]) == {"value": 4}
    #: verbe inconnu et action vide sont tous deux refusés par la grammaire
    with pytest.raises(ValueError):
        actions.run_action(client, ["shell", "rm -rf /"])
    with pytest.raises(ValueError):
        actions.run_action(client, [])
