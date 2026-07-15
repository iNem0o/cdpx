from cdpx.security.redaction import (
    MASK,
    RedactionContext,
    SecretRegistry,
    redact_action,
    redact_headers,
    redact_text,
    redact_tree,
    redact_url,
    secret_values_from_environment,
)


def test_secret_registry_stays_in_memory_and_hides_values_from_repr():
    """Le registre connaît ses secrets pour pouvoir les masquer, mais aucune
    introspection accidentelle (repr loggé, debugger) ne les révèle."""
    registry = SecretRegistry(["longer-secret", "secret"])

    #: le registre a bien retenu chaque valeur fournie
    assert len(registry) == 2
    #: la représentation debug ne trahit aucune des valeurs stockées
    assert "longer-secret" not in repr(registry)
    assert "secret" not in repr(registry)


def test_redact_url_removes_userinfo_fragment_and_masks_repeated_query_values():
    """Une URL sensible perd ses identifiants et son fragment, et chaque
    occurrence d'un paramètre suspect est masquée — même répétée ou vide —
    pendant que le rapport trace chaque champ nettoyé."""
    context = RedactionContext()

    value = redact_url(
        "https://alice:password@example.test:8443/path?Token=one&Token=two&empty=#section",
        context=context,
        path="$.url",
    )

    #: identifiants et fragment disparaissent; chaque occurrence du paramètre
    #: sensible est masquée, y compris la valeur vide
    assert value == "https://example.test:8443/path?Token=***&Token=***&empty=***"
    #: le rapport énumère précisément chaque champ nettoyé, pour l'audit
    assert context.report.as_dict() == {
        "redacted": True,
        "count": 4,
        "fields": [
            "$.url.fragment",
            "$.url.query.Token",
            "$.url.query.empty",
            "$.url.userinfo",
        ],
    }


def test_redact_url_reduces_data_payload_and_is_idempotent():
    """Le payload d'un data: URL est réduit à un marqueur, et une seconde
    passe de redaction est un no-op: plusieurs frontières peuvent filtrer
    la même valeur sans double comptage."""
    context = RedactionContext()
    source = "data:text/plain;name=private.txt;base64,c2VjcmV0"

    once = redact_url(source, context=context, path="$.src")
    twice = redact_url(once, context=context, path="$.src")

    #: le contenu embarqué est remplacé par le marqueur dédié
    assert once == "data:text/plain;cdpx-redacted,***"
    #: rejouer le filtre ne change rien et le champ n'est compté qu'une fois
    assert twice == once
    assert context.report.fields == ("$.src.data",)


def test_redact_url_masks_known_secrets_in_host_and_percent_encoded_path():
    """Un secret enregistré est masqué où qu'il se cache dans l'URL, hostname
    ou chemin percent-encodé: l'encodage ne fait pas échapper au filtre."""
    context = RedactionContext.from_secrets(["private-tenant", "encoded-secret"])

    value = redact_url(
        "https://private-tenant.example.test/reset/%65ncoded%2Dsecret?next=ok",
        context=context,
        path="$.url",
    )

    #: le tenant privé du host et le secret encodé du chemin sont masqués
    #: malgré leurs représentations différentes
    assert value == "https://***.example.test/reset/***?next=***"
    assert {"$.url.netloc", "$.url.path", "$.url.query.next"} <= set(context.report.fields)


def test_redact_url_fails_closed_for_malformed_values():
    """Une URL que le parseur ne sait pas décomposer est masquée en bloc:
    l'échec d'analyse ferme le filtre, il ne le contourne pas."""
    for value in ("https://[::1/path", "https://example.test/%ZZ", "https://bad host/path"):
        context = RedactionContext()
        #: chaque forme malformée est remplacée entièrement et signalée
        #: comme telle dans le rapport
        assert redact_url(value, context=context, path="$.url") == MASK
        assert context.report.fields == ("$.url.malformed",)


def test_redact_headers_is_case_insensitive_and_sanitizes_location():
    """Les en-têtes porteurs de credentials sont masqués quelle que soit la
    casse, Location est nettoyé comme une URL au lieu d'être perdu, et les
    en-têtes anodins passent intacts."""
    context = RedactionContext()
    headers = {
        "AUTHORIZATION": "Bearer top-secret",
        "Cookie": "session=top-secret",
        "set-cookie": "session=top-secret; HttpOnly",
        "X-Api-Key": "top-secret",
        "Location": "https://user:pass@example.test/next?code=abc#done",
        "Content-Type": "application/json",
    }

    redacted = redact_headers(headers, context=context, path="$.headers")

    #: seuls les en-têtes sensibles sont masqués; Location reste exploitable
    #: pour suivre la redirection mais perd identifiants, code et fragment
    assert redacted == {
        "AUTHORIZATION": MASK,
        "Cookie": MASK,
        "set-cookie": MASK,
        "X-Api-Key": MASK,
        "Location": "https://example.test/next?code=***",
        "Content-Type": "application/json",
    }
    #: le rapport détaille aussi les sous-champs nettoyés de Location
    assert set(context.report.fields) == {
        "$.headers.AUTHORIZATION",
        "$.headers.Cookie",
        "$.headers.set-cookie",
        "$.headers.X-Api-Key",
        "$.headers.Location.userinfo",
        "$.headers.Location.query.code",
        "$.headers.Location.fragment",
    }


def test_redact_headers_masks_token_secret_and_csrf_name_families():
    """La détection par nom couvre les familles token/secret/csrf dans leurs
    variantes (tirets, camelCase) sans masquer un en-tête qui contient le
    mot token par simple coïncidence."""
    headers = {
        "X-Debug-Token-Link": "https://example.test/_profiler/value",
        "X-Client-Secret": "secret-value",
        "X-CSRF-Token": "csrf-value",
        "X-CSRFToken": "django-csrf-value",
        "xAuthToken": "camel-auth-value",
        "X-Tokenizer-Version": "ordinary",
    }

    #: chaque famille sensible est masquée; le nom Tokenizer passe car le mot
    #: token n'y apparaît pas de façon isolée
    assert redact_headers(headers) == {
        "X-Debug-Token-Link": MASK,
        "X-Client-Secret": MASK,
        "X-CSRF-Token": MASK,
        "X-CSRFToken": MASK,
        "xAuthToken": MASK,
        "X-Tokenizer-Version": "ordinary",
    }


def test_redact_text_masks_registered_secrets_bearer_jwt_and_sensitive_urls():
    """Le texte libre est purgé de quatre familles de fuites: secret exact
    enregistré, jeton Bearer, JWT reconnu par sa structure, et URL porteuse
    de credentials."""
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123"
    context = RedactionContext.from_secrets(["exact-private-value"])
    text = (
        "secret=exact-private-value; Authorization: Bearer bearer-token; "
        f"jwt={jwt}; callback=https://alice:pass@example.test/cb?code=abc#fragment"
    )

    redacted = redact_text(text, context=context, path="$.message")

    #: aucune des valeurs sensibles ne survit, sous aucune de ses formes
    assert "exact-private-value" not in redacted
    assert "bearer-token" not in redacted
    assert jwt not in redacted
    assert "alice" not in redacted and "pass" not in redacted
    #: l'URL de callback reste corrélable et le compte de masques prouve
    #: l'absence de surmasquage
    assert "callback=https://example.test/cb?code=***" in redacted
    assert redacted.count(MASK) == 4
    #: le rapport pointe le champ texte dans son ensemble, pas un détail
    assert context.report.fields == ("$.message",)


def test_redact_text_avoids_aggressive_email_and_number_masking():
    """Emails et identifiants numériques ordinaires ne sont pas des secrets:
    le filtre n'appauvrit pas les diagnostics légitimes."""
    value = "contact=alice@example.test order=123456 status=ready"

    #: le texte anodin ressort strictement identique — zéro faux positif
    assert redact_text(value) == value


def test_redact_text_distinguishes_javascript_data_properties_from_data_urls():
    """La réduction des data: URLs ne confond pas la propriété JavaScript
    nommée data avec le schéma data:, seul ce dernier transporte un payload
    à masquer."""
    value = (
        "const selection={data:function(value){return value},meta:{data:[1,2]}};"
        'const icon = "data:image/png;base64,cHJpdmF0ZQ==";'
    )

    redacted = redact_text(value)

    #: les constructions JavaScript autour du mot data restent intactes
    assert "{data:function(value)" in redacted
    assert "{data:[1,2]}" in redacted
    #: le véritable data: URL est, lui, réduit à son marqueur
    assert '"data:image/png;cdpx-redacted,***"' in redacted


def test_environment_secret_discovery_is_name_scoped_and_ignores_tiny_values():
    """Seules les variables au nom évocateur et à la valeur non triviale
    alimentent le registre: pas d'aspiration aveugle de l'environnement."""
    values = secret_values_from_environment(
        {
            "CHECKOUT_PASSWORD": "private-password",
            "SERVICE_TOKEN": "token-value",
            "MONKEY": "not-a-secret-name",
            "SHORT_KEY": "x",
            "PATH": "/usr/bin",
        }
    )

    #: les noms de la famille password/token sont retenus; nom anodin, valeur
    #: d'un seul caractère et PATH sont écartés du registre
    assert values == ["private-password", "token-value"]


def test_redact_action_masks_type_eval_and_cookie_values():
    """Chaque verbe journalisé a sa politique: type masque le texte saisi,
    eval masque toute l'expression, cookies set masque la valeur et nettoie
    l'URL — l'action reste identifiable sans livrer la valeur secrète."""
    type_context = RedactionContext()
    eval_context = RedactionContext()
    cookie_context = RedactionContext()

    #: le texte tapé est masqué, sélecteur et options restent lisibles
    assert redact_action(
        ["type", "#password", "hunter2", "--clear"],
        context=type_context,
    ) == ["type", "#password", MASK, "--clear"]
    #: une expression eval est opaque: chaque argument est masqué en bloc
    assert redact_action(
        ["eval", "window.secret", "+", "document.cookie"],
        context=eval_context,
    ) == ["eval", MASK, MASK, MASK]
    #: la valeur du cookie est masquée et son URL nettoyée; le nom du cookie
    #: reste utile à la corrélation
    assert redact_action(
        [
            "cookies",
            "set",
            "--name",
            "session",
            "--value",
            "cookie-secret",
            "--url",
            "https://example.test/?token=value",
        ],
        context=cookie_context,
    ) == [
        "cookies",
        "set",
        "--name",
        "session",
        "--value",
        MASK,
        "--url",
        "https://example.test/?token=***",
    ]
    #: chaque contexte rapporte exactement les positions argv touchées
    assert type_context.report.fields == ("$[2]",)
    assert eval_context.report.fields == ("$[1]", "$[2]", "$[3]")
    assert set(cookie_context.report.fields) == {"$[5]", "$[7].query.token"}


def test_redact_action_supports_structured_actions():
    """La redaction d'action s'applique aussi à la forme dict du journal:
    texte masqué et URL nettoyée sans casser la structure rejouable."""
    context = RedactionContext()

    action = redact_action(
        {
            "verb": "type",
            "selector": "#password",
            "text": "typed-secret",
            "url": "https://example.test/form?csrf=abc",
        },
        context=context,
        path="$.action",
    )

    #: seuls le texte saisi et le paramètre sensible de l'URL sont masqués,
    #: le reste de l'action demeure exploitable
    assert action == {
        "verb": "type",
        "selector": "#password",
        "text": MASK,
        "url": "https://example.test/form?csrf=***",
    }
    assert set(context.report.fields) == {
        "$.action.text",
        "$.action.url.query.csrf",
    }


def test_redact_tree_uses_context_for_headers_actions_urls_and_sensitive_keys():
    """Le nettoyage transversal d'un arbre JSON applique la règle adaptée à
    chaque nœud (URL, headers, action, clé sensible, texte libre), y compris
    en profondeur dans les listes imbriquées."""
    context = RedactionContext.from_secrets(["known-secret"])
    payload = {
        "url": "https://example.test/path?token=known-secret#fragment",
        "headers": {
            "authorization": "Bearer known-secret",
            "Content-Type": "text/plain",
        },
        "action": ["type", "#name", "Ada"],
        "token": "known-secret",
        "typed": "Ada",
        "message": "failure contained known-secret",
        "nested": [{"href": "https://example.test/next?page=2"}],
        "ok": True,
    }

    redacted = redact_tree(payload, context=context)

    #: chaque famille de nœuds reçoit son traitement dédié; le message libre
    #: perd le secret connu mais garde son sens, l'anodin survit
    assert redacted == {
        "url": "https://example.test/path?token=***",
        "headers": {"authorization": MASK, "Content-Type": "text/plain"},
        "action": ["type", "#name", MASK],
        "token": MASK,
        "typed": MASK,
        "message": "failure contained ***",
        "nested": [{"href": "https://example.test/next?page=***"}],
        "ok": True,
    }
    #: le rapport global atteste l'ampleur réelle du nettoyage effectué
    assert context.report.redacted is True
    assert context.report.count >= 8


def test_redact_tree_normalizes_camel_case_sensitive_keys():
    """La détection des clés sensibles comprend le camelCase des payloads CDP
    sans masquer les clés ordinaires voisines."""
    payload = {
        "clientSecret": "one",
        "accessToken": "two",
        "csrfToken": "three",
        "webSocketDebuggerUrl": "ws://127.0.0.1/private",
        "ordinaryValue": "kept",
    }

    #: les clés camelCase des familles secret/token/csrf/debugger sont
    #: masquées, la clé ordinaire est conservée
    assert redact_tree(payload) == {
        "clientSecret": MASK,
        "accessToken": MASK,
        "csrfToken": MASK,
        "webSocketDebuggerUrl": MASK,
        "ordinaryValue": "kept",
    }


def test_redaction_is_idempotent_for_a_complete_tree_and_report():
    """Rejouer la redaction sur une sortie déjà nettoyée est un no-op total:
    ni double masquage ni gonflement du rapport quand primitive, CLI et
    artefact filtrent successivement la même donnée."""
    context = RedactionContext.from_secrets(["private-value"])
    payload = {
        "url": "https://user:pass@example.test/?token=private-value#fragment",
        "headers": {"Cookie": "session=private-value"},
        "action": ["eval", "private-value"],
        "message": "Bearer private-value",
    }

    once = redact_tree(payload, context=context)
    report_once = context.report
    twice = redact_tree(once, context=context)

    #: la seconde passe ne modifie ni l'arbre ni le rapport accumulé
    assert twice == once
    assert context.report == report_once
