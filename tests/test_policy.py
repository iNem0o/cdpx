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


def test_team_context_requires_run_target_and_origins():
    with pytest.raises(PolicyError, match="run-id"):
        ExecutionContext.team(
            run_id="", target_id="T1", authority="observation", origins="http://x.test"
        )
    with pytest.raises(PolicyError, match="target"):
        ExecutionContext.team(
            run_id="R1", target_id="", authority="observation", origins="http://x.test"
        )
    with pytest.raises(PolicyError, match="CDPX_ORIGINS"):
        ExecutionContext.team(run_id="R1", target_id="T1", authority="observation", origins="")


def test_origin_patterns_are_canonical_and_fail_closed():
    assert parse_origins("HTTP://*.TEST,http://localhost:*") == (
        "http://*.test",
        "http://localhost:*",
    )
    for invalid in ("", "*", "*://*", "http://x.test/path", "http://u:p@x.test", "file:///"):
        with pytest.raises(PolicyError):
            parse_origins(invalid, required=True)


def test_team_origins_apply_to_observation_and_interaction():
    patterns = parse_origins("http://*.test,http://127.0.0.1:*")
    assert_url_allowed("http://shop.test/page?token=secret", patterns)
    assert_url_allowed("http://127.0.0.1:8899/", patterns)
    with pytest.raises(PolicyError, match="origine refusée"):
        assert_url_allowed("https://prod.example/", patterns)
    with pytest.raises(PolicyError, match="origine HTTP"):
        assert_url_allowed("about:blank", patterns)


def test_origin_matching_is_structured_for_ipv6_and_wildcard_ports():
    ipv6 = parse_origins("http://[::1]:*")
    assert_url_allowed("http://[::1]:9222/page", ipv6)
    assert_url_allowed("http://[::1]/default-port", ipv6)
    with pytest.raises(PolicyError, match="origine refusée"):
        assert_url_allowed("http://[::2]:9222/page", ipv6)

    subdomains = parse_origins("https://*.example.test")
    assert_url_allowed("https://shop.example.test/path", subdomains)
    assert_url_allowed("https://deep.shop.example.test/path", subdomains)
    with pytest.raises(PolicyError, match="origine refusée"):
        assert_url_allowed("https://example.test/path", subdomains)


def test_loopback_validates_discovery_and_published_websocket():
    assert_loopback_endpoint("127.0.0.1", "ws://localhost:9333/devtools/page/T1")
    assert_loopback_endpoint("::1", "ws://[::1]:9333/devtools/page/T1")
    with pytest.raises(PolicyError, match="loopback"):
        assert_loopback_endpoint("chrome.internal", "ws://127.0.0.1:9222/devtools/page/T1")
    with pytest.raises(PolicyError, match="WebSocket"):
        assert_loopback_endpoint("127.0.0.1", "ws://10.0.0.4:9222/devtools/page/T1")


def test_target_must_be_the_owned_page_target():
    target = {
        "id": "T1",
        "type": "page",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/T1",
    }
    assert validate_target(target, "T1") == target
    with pytest.raises(PolicyError, match="attribué"):
        validate_target(target, "T2")
    with pytest.raises(PolicyError, match="type page"):
        validate_target({**target, "type": "service_worker"}, "T1")
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
        ("tabs", ["new"], Authority.PRIVILEGED),
    ],
)
def test_command_authority_matrix(command, action, expected):
    assert authority_for(command, action) is expected


def test_unknown_commands_and_insufficient_grants_fail_closed():
    with pytest.raises(PolicyError, match="non classée"):
        authority_for("future-command")
    context = ExecutionContext.team(
        run_id="R1",
        target_id="T1",
        authority="observation",
        origins="http://*.test",
    )
    assert_authorized(context, "text")
    with pytest.raises(PolicyError, match="requiert interaction"):
        assert_authorized(context, "click")
    with pytest.raises(PolicyError, match="requiert privileged"):
        assert_authorized(context, "eval")
