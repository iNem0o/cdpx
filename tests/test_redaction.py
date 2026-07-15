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
    registry = SecretRegistry(["longer-secret", "secret"])

    assert len(registry) == 2
    assert "longer-secret" not in repr(registry)
    assert "secret" not in repr(registry)


def test_redact_url_removes_userinfo_fragment_and_masks_repeated_query_values():
    context = RedactionContext()

    value = redact_url(
        "https://alice:password@example.test:8443/path?Token=one&Token=two&empty=#section",
        context=context,
        path="$.url",
    )

    assert value == "https://example.test:8443/path?Token=***&Token=***&empty=***"
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
    context = RedactionContext()
    source = "data:text/plain;name=private.txt;base64,c2VjcmV0"

    once = redact_url(source, context=context, path="$.src")
    twice = redact_url(once, context=context, path="$.src")

    assert once == "data:text/plain;cdpx-redacted,***"
    assert twice == once
    assert context.report.fields == ("$.src.data",)


def test_redact_url_masks_known_secrets_in_host_and_percent_encoded_path():
    context = RedactionContext.from_secrets(["private-tenant", "encoded-secret"])

    value = redact_url(
        "https://private-tenant.example.test/reset/%65ncoded%2Dsecret?next=ok",
        context=context,
        path="$.url",
    )

    assert value == "https://***.example.test/reset/***?next=***"
    assert {"$.url.netloc", "$.url.path", "$.url.query.next"} <= set(context.report.fields)


def test_redact_url_fails_closed_for_malformed_values():
    for value in ("https://[::1/path", "https://example.test/%ZZ", "https://bad host/path"):
        context = RedactionContext()
        assert redact_url(value, context=context, path="$.url") == MASK
        assert context.report.fields == ("$.url.malformed",)


def test_redact_headers_is_case_insensitive_and_sanitizes_location():
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

    assert redacted == {
        "AUTHORIZATION": MASK,
        "Cookie": MASK,
        "set-cookie": MASK,
        "X-Api-Key": MASK,
        "Location": "https://example.test/next?code=***",
        "Content-Type": "application/json",
    }
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
    headers = {
        "X-Debug-Token-Link": "https://example.test/_profiler/value",
        "X-Client-Secret": "secret-value",
        "X-CSRF-Token": "csrf-value",
        "X-CSRFToken": "django-csrf-value",
        "xAuthToken": "camel-auth-value",
        "X-Tokenizer-Version": "ordinary",
    }

    assert redact_headers(headers) == {
        "X-Debug-Token-Link": MASK,
        "X-Client-Secret": MASK,
        "X-CSRF-Token": MASK,
        "X-CSRFToken": MASK,
        "xAuthToken": MASK,
        "X-Tokenizer-Version": "ordinary",
    }


def test_redact_text_masks_registered_secrets_bearer_jwt_and_sensitive_urls():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature123"
    context = RedactionContext.from_secrets(["exact-private-value"])
    text = (
        "secret=exact-private-value; Authorization: Bearer bearer-token; "
        f"jwt={jwt}; callback=https://alice:pass@example.test/cb?code=abc#fragment"
    )

    redacted = redact_text(text, context=context, path="$.message")

    assert "exact-private-value" not in redacted
    assert "bearer-token" not in redacted
    assert jwt not in redacted
    assert "alice" not in redacted and "pass" not in redacted
    assert "callback=https://example.test/cb?code=***" in redacted
    assert redacted.count(MASK) == 4
    assert context.report.fields == ("$.message",)


def test_redact_text_avoids_aggressive_email_and_number_masking():
    value = "contact=alice@example.test order=123456 status=ready"

    assert redact_text(value) == value


def test_redact_text_distinguishes_javascript_data_properties_from_data_urls():
    value = (
        "const selection={data:function(value){return value},meta:{data:[1,2]}};"
        'const icon = "data:image/png;base64,cHJpdmF0ZQ==";'
    )

    redacted = redact_text(value)

    assert "{data:function(value)" in redacted
    assert "{data:[1,2]}" in redacted
    assert '"data:image/png;cdpx-redacted,***"' in redacted


def test_environment_secret_discovery_is_name_scoped_and_ignores_tiny_values():
    values = secret_values_from_environment(
        {
            "CHECKOUT_PASSWORD": "private-password",
            "SERVICE_TOKEN": "token-value",
            "MONKEY": "not-a-secret-name",
            "SHORT_KEY": "x",
            "PATH": "/usr/bin",
        }
    )

    assert values == ["private-password", "token-value"]


def test_redact_action_masks_type_eval_and_cookie_values():
    type_context = RedactionContext()
    eval_context = RedactionContext()
    cookie_context = RedactionContext()

    assert redact_action(
        ["type", "#password", "hunter2", "--clear"],
        context=type_context,
    ) == ["type", "#password", MASK, "--clear"]
    assert redact_action(
        ["eval", "window.secret", "+", "document.cookie"],
        context=eval_context,
    ) == ["eval", MASK, MASK, MASK]
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
    assert type_context.report.fields == ("$[2]",)
    assert eval_context.report.fields == ("$[1]", "$[2]", "$[3]")
    assert set(cookie_context.report.fields) == {"$[5]", "$[7].query.token"}


def test_redact_action_supports_structured_actions():
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
    assert context.report.redacted is True
    assert context.report.count >= 8


def test_redact_tree_normalizes_camel_case_sensitive_keys():
    payload = {
        "clientSecret": "one",
        "accessToken": "two",
        "csrfToken": "three",
        "webSocketDebuggerUrl": "ws://127.0.0.1/private",
        "ordinaryValue": "kept",
    }

    assert redact_tree(payload) == {
        "clientSecret": MASK,
        "accessToken": MASK,
        "csrfToken": MASK,
        "webSocketDebuggerUrl": MASK,
        "ordinaryValue": "kept",
    }


def test_redaction_is_idempotent_for_a_complete_tree_and_report():
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

    assert twice == once
    assert context.report == report_once
