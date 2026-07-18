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
    """The registry knows its secrets so it can mask them, but no accidental
    introspection (logged repr, debugger) reveals them."""
    registry = SecretRegistry(["longer-secret", "secret"])

    #: the registry did retain every provided value
    assert len(registry) == 2
    #: the debug representation betrays none of the stored values
    assert "longer-secret" not in repr(registry)
    assert "secret" not in repr(registry)


def test_redact_url_removes_userinfo_fragment_and_masks_repeated_query_values():
    """A sensitive URL loses its credentials and fragment, and every
    occurrence of a suspect parameter is masked — even repeated or empty —
    while the report traces each cleaned field."""
    context = RedactionContext()

    value = redact_url(
        "https://alice:password@example.test:8443/path?Token=one&Token=two&empty=#section",
        context=context,
        path="$.url",
    )

    #: credentials and fragment disappear; every occurrence of the
    #: sensitive parameter is masked, including the empty value
    assert value == "https://example.test:8443/path?Token=***&Token=***&empty=***"
    #: the report precisely enumerates each cleaned field, for the audit
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
    """The payload of a data: URL is reduced to a marker, and a second
    redaction pass is a no-op: several boundaries can filter the same
    value without double counting."""
    context = RedactionContext()
    source = "data:text/plain;name=private.txt;base64,c2VjcmV0"

    once = redact_url(source, context=context, path="$.src")
    twice = redact_url(once, context=context, path="$.src")

    #: the embedded content is replaced with the dedicated marker
    assert once == "data:text/plain;cdpx-redacted,***"
    #: replaying the filter changes nothing and the field is counted only once
    assert twice == once
    assert context.report.fields == ("$.src.data",)


def test_redact_url_masks_known_secrets_in_host_and_percent_encoded_path():
    """A registered secret is masked wherever it hides in the URL, hostname,
    or percent-encoded path: encoding does not let it escape the filter."""
    context = RedactionContext.from_secrets(["private-tenant", "encoded-secret"])

    value = redact_url(
        "https://private-tenant.example.test/reset/%65ncoded%2Dsecret?next=ok",
        context=context,
        path="$.url",
    )

    #: the private tenant in the host and the encoded secret in the path are
    #: masked despite their different representations
    assert value == "https://***.example.test/reset/***?next=***"
    assert {"$.url.netloc", "$.url.path", "$.url.query.next"} <= set(context.report.fields)


def test_redact_url_fails_closed_for_malformed_values():
    """A URL that the parser cannot decompose is masked wholesale: parsing
    failure closes the filter, it does not bypass it."""
    for value in ("https://[::1/path", "https://example.test/%ZZ", "https://bad host/path"):
        context = RedactionContext()
        #: each malformed form is replaced entirely and flagged
        #: as such in the report
        assert redact_url(value, context=context, path="$.url") == MASK
        assert context.report.fields == ("$.url.malformed",)


def test_redact_headers_is_case_insensitive_and_sanitizes_location():
    """Headers carrying credentials are masked regardless of case,
    Location is cleaned like a URL instead of being lost, and
    ordinary headers pass through intact."""
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

    #: only sensitive headers are masked; Location stays usable
    #: to follow the redirect but loses credentials, code, and fragment
    assert redacted == {
        "AUTHORIZATION": MASK,
        "Cookie": MASK,
        "set-cookie": MASK,
        "X-Api-Key": MASK,
        "Location": "https://example.test/next?code=***",
        "Content-Type": "application/json",
    }
    #: the report also details the cleaned sub-fields of Location
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
    """Name-based detection covers the token/secret/csrf families in their
    variants (hyphenated, camelCase) without masking a header that contains
    the word token by mere coincidence."""
    headers = {
        "X-Debug-Token-Link": "https://example.test/_profiler/value",
        "X-Client-Secret": "secret-value",
        "X-CSRF-Token": "csrf-value",
        "X-CSRFToken": "django-csrf-value",
        "xAuthToken": "camel-auth-value",
        "X-Tokenizer-Version": "ordinary",
    }

    #: every sensitive family is masked; the name Tokenizer passes through
    #: because the word token does not appear in it in isolation
    assert redact_headers(headers) == {
        "X-Debug-Token-Link": MASK,
        "X-Client-Secret": MASK,
        "X-CSRF-Token": MASK,
        "X-CSRFToken": MASK,
        "xAuthToken": MASK,
        "X-Tokenizer-Version": "ordinary",
    }


def test_redact_text_masks_registered_secrets_bearer_jwt_and_sensitive_urls():
    """Free text is purged of four families of leaks: exact registered
    secret, Bearer token, JWT recognized by its structure, and URL carrying
    credentials."""
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123"
    context = RedactionContext.from_secrets(["exact-private-value"])
    text = (
        "secret=exact-private-value; Authorization: Bearer bearer-token; "
        f"jwt={jwt}; callback=https://alice:pass@example.test/cb?code=abc#fragment"
    )

    redacted = redact_text(text, context=context, path="$.message")

    #: none of the sensitive values survive, in any of their forms
    assert "exact-private-value" not in redacted
    assert "bearer-token" not in redacted
    assert jwt not in redacted
    assert "alice" not in redacted and "pass" not in redacted
    #: the callback URL stays correlatable and the mask count proves
    #: the absence of over-masking
    assert "callback=https://example.test/cb?code=***" in redacted
    assert redacted.count(MASK) == 4
    #: the report points to the text field as a whole, not a detail
    assert context.report.fields == ("$.message",)


def test_redact_text_avoids_aggressive_email_and_number_masking():
    """Ordinary emails and numeric identifiers are not secrets: the filter
    does not impoverish legitimate diagnostics."""
    value = "contact=alice@example.test order=123456 status=ready"

    #: ordinary text comes out strictly identical — zero false positives
    assert redact_text(value) == value


def test_redact_text_distinguishes_javascript_data_properties_from_data_urls():
    """The reduction of data: URLs does not confuse the JavaScript property
    named data with the data: scheme, only the latter carries a payload
    to mask."""
    value = (
        "const selection={data:function(value){return value},meta:{data:[1,2]}};"
        'const icon = "data:image/png;base64,cHJpdmF0ZQ==";'
    )

    redacted = redact_text(value)

    #: JavaScript constructs around the word data stay intact
    assert "{data:function(value)" in redacted
    assert "{data:[1,2]}" in redacted
    #: the actual data: URL, on the other hand, is reduced to its marker
    assert '"data:image/png;cdpx-redacted,***"' in redacted


def test_environment_secret_discovery_is_name_scoped_and_ignores_tiny_values():
    """Only variables with an evocative name and a non-trivial value
    feed the registry: no blind vacuuming of the environment."""
    values = secret_values_from_environment(
        {
            "CHECKOUT_PASSWORD": "private-password",
            "SERVICE_TOKEN": "token-value",
            "MONKEY": "not-a-secret-name",
            "SHORT_KEY": "x",
            "PATH": "/usr/bin",
        }
    )

    #: names in the password/token family are retained; an ordinary name, a
    #: single-character value, and PATH are excluded from the registry
    assert values == ["private-password", "token-value"]


def test_redact_action_masks_type_eval_and_cookie_values():
    """Each logged verb has its own policy: type masks the typed text,
    eval masks the whole expression, cookies set masks the value and cleans
    the URL — the action stays identifiable without delivering the secret
    value."""
    type_context = RedactionContext()
    eval_context = RedactionContext()
    cookie_context = RedactionContext()

    #: the typed text is masked, selector and options stay readable
    assert redact_action(
        ["type", "#password", "hunter2", "--clear"],
        context=type_context,
    ) == ["type", "#password", MASK, "--clear"]
    #: an eval expression is opaque: every argument is masked wholesale
    assert redact_action(
        ["eval", "window.secret", "+", "document.cookie"],
        context=eval_context,
    ) == ["eval", MASK, MASK, MASK]
    #: the cookie value is masked and its URL cleaned; the cookie name
    #: stays useful for correlation
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
    #: each context reports exactly the argv positions touched
    assert type_context.report.fields == ("$[2]",)
    assert eval_context.report.fields == ("$[1]", "$[2]", "$[3]")
    assert set(cookie_context.report.fields) == {"$[5]", "$[7].query.token"}


def test_redact_action_supports_structured_actions():
    """Action redaction also applies to the dict form of the log:
    masked text and cleaned URL without breaking the replayable structure."""
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

    #: only the typed text and the sensitive URL parameter are masked,
    #: the rest of the action remains usable
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
    """The cross-cutting cleanup of a JSON tree applies the rule suited to
    each node (URL, headers, action, sensitive key, free text), including
    deep within nested lists."""
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

    #: each family of nodes receives its dedicated treatment; the free
    #: message loses the known secret but keeps its meaning, the ordinary
    #: value survives
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
    #: the overall report attests to the real extent of the cleanup performed
    assert context.report.redacted is True
    assert context.report.count >= 8


def test_redact_tree_normalizes_camel_case_sensitive_keys():
    """Sensitive key detection understands the camelCase of CDP payloads
    without masking neighboring ordinary keys."""
    payload = {
        "clientSecret": "one",
        "accessToken": "two",
        "csrfToken": "three",
        "webSocketDebuggerUrl": "ws://127.0.0.1/private",
        "ordinaryValue": "kept",
    }

    #: camelCase keys from the secret/token/csrf/debugger families are
    #: masked, the ordinary key is kept
    assert redact_tree(payload) == {
        "clientSecret": MASK,
        "accessToken": MASK,
        "csrfToken": MASK,
        "webSocketDebuggerUrl": MASK,
        "ordinaryValue": "kept",
    }


def test_redaction_is_idempotent_for_a_complete_tree_and_report():
    """Replaying redaction on an already-cleaned output is a total no-op:
    neither double masking nor report inflation when primitive, CLI, and
    artifact successively filter the same data."""
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

    #: the second pass modifies neither the tree nor the accumulated report
    assert twice == once
    assert context.report == report_once
