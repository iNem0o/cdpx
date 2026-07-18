from __future__ import annotations

import pytest

from cdpx.action_model import ClickAction, EvalAction, GotoAction, KeyAction, TypeAction, WaitAction
from cdpx.policy import (
    Authority,
    ExecutionContext,
    PolicyError,
    action_authority,
    assert_authorized,
    assert_loopback_endpoint,
    assert_url_allowed,
    authority_for,
    command_semantics,
    parse_origins,
    validate_target,
)


def test_execution_context_requires_session_run_target_and_origins():
    """The execution context refuses to be built halfway: without a run-id,
    an assigned target, or declared origins, no command can even be
    evaluated."""
    #: each missing parameter is refused with a message naming the
    #: faulty field, for an immediate diagnosis on the supervisor side
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
    """Origin patterns are normalized (lowercase) and every ambiguous form
    — total wildcard, path, credentials, file: — is rejected: the
    perimeter cannot widen by a syntax accident."""
    #: case is canonicalized so origin comparison stays stable
    assert parse_origins("HTTP://*.TEST,http://localhost:*") == (
        "http://*.test",
        "http://localhost:*",
    )
    for invalid in ("", "*", "*://*", "http://x.test/path", "http://u:p@x.test", "file:///"):
        #: every pattern that is too permissive or malformed is rejected
        #: rather than interpreted leniently
        with pytest.raises(PolicyError):
            parse_origins(invalid, required=True)


def test_session_origins_apply_to_observation_and_interaction():
    """The session origin list exactly delimits the reachable hosts: a URL
    outside the perimeter or non-HTTP is refused, even for plain
    reading."""
    patterns = parse_origins("http://*.test,http://127.0.0.1:*")
    #: URLs conforming to the declared patterns pass without exception
    assert_url_allowed("http://shop.test/page?token=secret", patterns)
    assert_url_allowed("http://127.0.0.1:8899/", patterns)
    #: a host outside the list is blocked before any network access
    with pytest.raises(PolicyError, match="origin rejected"):
        assert_url_allowed("https://prod.example/", patterns)
    #: non-HTTP schemes have no comparable origin: explicit refusal
    with pytest.raises(PolicyError, match="HTTP"):
        assert_url_allowed("about:blank", patterns)


def test_origin_matching_is_structured_for_ipv6_and_wildcard_ports():
    """Origin comparison is structural, not textual: bracketed IPv6, wildcard
    port, and subdomain wildcard all match correctly without letting through
    a neighboring host that merely resembles them."""
    ipv6 = parse_origins("http://[::1]:*")
    #: the port wildcard covers any port, including the implicit one
    assert_url_allowed("http://[::1]:9222/page", ipv6)
    assert_url_allowed("http://[::1]/default-port", ipv6)
    #: another IPv6 address is refused despite its textual resemblance
    with pytest.raises(PolicyError, match="origin rejected"):
        assert_url_allowed("http://[::2]:9222/page", ipv6)

    subdomains = parse_origins("https://*.example.test")
    #: the subdomain wildcard matches at any depth
    assert_url_allowed("https://shop.example.test/path", subdomains)
    assert_url_allowed("https://deep.shop.example.test/path", subdomains)
    #: but never the bare domain: the wildcard does not include the apex
    with pytest.raises(PolicyError, match="origin rejected"):
        assert_url_allowed("https://example.test/path", subdomains)


def test_loopback_validates_discovery_and_published_websocket():
    """The client only speaks to a local Chrome: the discovery host AND
    the WebSocket endpoint it publishes must both be loopback."""
    #: the IPv4 loopback, local hostname, and IPv6 forms are accepted
    assert_loopback_endpoint("127.0.0.1", "ws://localhost:9333/devtools/page/T1")
    assert_loopback_endpoint("::1", "ws://[::1]:9333/devtools/page/T1")
    #: a remote discovery host is refused even if the announced WS is local
    with pytest.raises(PolicyError, match="loopback"):
        assert_loopback_endpoint("chrome.internal", "ws://127.0.0.1:9222/devtools/page/T1")
    #: a WS published to an external IP is refused even with local discovery:
    #: no bouncing off-machine
    with pytest.raises(PolicyError, match="WebSocket"):
        assert_loopback_endpoint("127.0.0.1", "ws://10.0.0.4:9222/devtools/page/T1")


def test_target_must_be_the_owned_page_target():
    """The session only drives the target assigned to it: correct
    identifier, page type, and a present WebSocket endpoint, otherwise refusal."""
    target = {
        "id": "T1",
        "type": "page",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/T1",
    }
    #: the assigned, compliant target is returned as-is
    assert validate_target(target, "T1") == target
    #: an identifier other than the assigned one is refused: no tab-hopping
    with pytest.raises(PolicyError, match="not assigned"):
        validate_target(target, "T2")
    #: a non-page target (worker, extension) is outside the driving perimeter
    with pytest.raises(PolicyError, match="type page"):
        validate_target({**target, "type": "service_worker"}, "T1")
    #: without a WebSocket endpoint the target cannot be verifiably driven
    with pytest.raises(PolicyError, match="WebSocket"):
        validate_target({"id": "T1", "type": "page"}, "T1")


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("goto", Authority.OBSERVATION),
        ("text", Authority.OBSERVATION),
        ("network", Authority.OBSERVATION),
        ("click", Authority.INTERACTION),
        ("type", Authority.INTERACTION),
        ("key", Authority.INTERACTION),
        ("eval", Authority.PRIVILEGED),
        ("cookies", Authority.PRIVILEGED),
        ("storage", Authority.PRIVILEGED),
        ("profiler", Authority.PRIVILEGED),
        ("intercept", Authority.PRIVILEGED),
        ("emulate", Authority.PRIVILEGED),
        ("vitals", Authority.OBSERVATION),
        ("dom-diff", Authority.OBSERVATION),
        ("record", Authority.PRIVILEGED),
        ("replay", Authority.PRIVILEGED),
        ("scenario", Authority.PRIVILEGED),
        ("tabs", Authority.OBSERVATION),
    ],
)
def test_command_authority_matrix(command, expected):
    """Each CLI command has a base authority independent of its action."""
    #: the command -> authority matrix is the exact contract that the
    #: authorization gate applies before any execution
    assert authority_for(command) is expected


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (GotoAction("http://site.test"), Authority.OBSERVATION),
        (WaitAction("#ready"), Authority.OBSERVATION),
        (ClickAction("#go"), Authority.INTERACTION),
        (TypeAction("#name", "Ada"), Authority.INTERACTION),
        (KeyAction("Enter"), Authority.INTERACTION),
        (EvalAction("document.title"), Authority.PRIVILEGED),
    ],
)
def test_typed_action_authority_matrix(action, expected):
    assert action_authority(action) is expected


def test_unknown_commands_and_insufficient_grants_fail_closed():
    """An unknown command has no implicit authority and an insufficient grant
    blocks before execution: the policy fails closed."""
    #: an unclassified command is refused instead of inheriting a default
    with pytest.raises(PolicyError, match="not classified"):
        authority_for("future-command")
    context = ExecutionContext.create(
        run_id="R1",
        target_id="T1",
        authority="observation",
        origins="http://*.test",
        session_id="S1",
    )
    #: the observation grant covers reading without friction
    assert_authorized(context, "text")
    #: interaction and privileged each require an explicit elevation,
    #: named in the error to guide the supervisor
    with pytest.raises(PolicyError, match="requires interaction"):
        assert_authorized(context, "click")
    with pytest.raises(PolicyError, match="requires privileged"):
        assert_authorized(context, "eval")


def test_session_lifecycle_is_outside_browser_authority_matrix():
    """The lifecycle has its own capability boundary: ``start`` creates
    the browser grant, while ``status`` and ``stop`` require the exact
    manifest identity instead of simulating a privileged CDP command."""
    with pytest.raises(PolicyError, match="lifecycle command outside"):
        command_semantics("session")
