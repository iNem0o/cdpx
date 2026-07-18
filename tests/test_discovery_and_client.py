"""HTTP discovery (/json) and WebSocket client, validated against the CDP mock."""

import json

import pytest

from cdpx import client as client_module
from cdpx import discovery
from cdpx.client import CDPClient, CDPError, CDPTimeout, CDPTransportError


def _connect(mock) -> CDPClient:
    target_id = next(iter(mock.targets))
    target = discovery.pick_page("127.0.0.1", mock.http_port, target_id)
    return CDPClient(target["webSocketDebuggerUrl"], timeout=5)


# -- discovery -------------------------------------------------------------------


def test_list_targets(mock):
    """Discovery /json exposes the mock's single page with a strictly
    loopback debugging WebSocket endpoint, ready for the client."""
    targets = discovery.list_targets("127.0.0.1", mock.http_port)
    #: a single page-type target is discoverable, and its control URL
    #: stays confined to the loopback interface
    assert len(targets) == 1
    assert targets[0]["type"] == "page"
    assert targets[0]["webSocketDebuggerUrl"].startswith("ws://127.0.0.1:")


def test_version(mock):
    """/json/version announces the CDP protocol version the client knows
    how to speak — the minimal contract before any WebSocket dialogue."""
    v = discovery.version("127.0.0.1", mock.http_port)
    #: the announced version matches the protocol implemented by the client
    assert v["Protocol-Version"] == "1.3"


def test_loopback_discovery_ignores_environment_proxy(mock, monkeypatch):
    """A hostile proxy declared in the environment never hijacks discovery
    traffic: /json calls stay on a direct connection."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    #: discovery succeeds even though an unreachable proxy is imposed by
    #: the environment, proof that loopback bypasses it
    assert discovery.version("127.0.0.1", mock.http_port)["Protocol-Version"] == "1.3"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('{"not": "a list"}', "JSON array required"),
        ('[{"type": "page"}]', "without valid id"),
        ("not-json", "invalid JSON"),
    ],
)
def test_list_targets_rejects_malformed_discovery_shapes(monkeypatch, payload, message):
    monkeypatch.setattr(discovery, "_http", lambda *_args, **_kwargs: payload)

    with pytest.raises(discovery.DiscoveryError, match=message):
        discovery.list_targets("127.0.0.1", 9222)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('[{"id":"T1","url":3}]', "target\\[0\\]\\.url text required"),
        (
            '[{"id":"T1","webSocketDebuggerUrl":false}]',
            "webSocketDebuggerUrl text required",
        ),
    ],
)
def test_list_targets_rejects_non_string_declared_fields(monkeypatch, payload, message):
    monkeypatch.setattr(discovery, "_http", lambda *_args, **_kwargs: payload)

    with pytest.raises(discovery.DiscoveryError, match=message):
        discovery.list_targets("127.0.0.1", 9222)


def test_version_rejects_non_string_declared_fields(monkeypatch):
    monkeypatch.setattr(discovery, "_http", lambda *_args, **_kwargs: '{"Browser":3}')

    with pytest.raises(discovery.DiscoveryError, match="version\\.Browser text required"):
        discovery.version("127.0.0.1", 9222)


def test_version_decode_error_keeps_http_context(monkeypatch):
    monkeypatch.setattr(discovery, "_http", lambda *_args, **_kwargs: "not-json")

    with pytest.raises(discovery.DiscoveryError, match="invalid JSON") as error:
        discovery.version("127.0.0.1", 9222)

    assert error.value.method == "GET"
    assert error.value.url == "http://127.0.0.1:9222/json/version"
    assert isinstance(error.value.__cause__, json.JSONDecodeError)


def test_list_decode_error_keeps_http_context(monkeypatch):
    monkeypatch.setattr(discovery, "_http", lambda *_args, **_kwargs: "not-json")

    with pytest.raises(discovery.DiscoveryError, match="invalid JSON") as error:
        discovery.list_targets("127.0.0.1", 9222)

    assert error.value.method == "GET"
    assert error.value.url == "http://127.0.0.1:9222/json/list"
    assert isinstance(error.value.__cause__, json.JSONDecodeError)


def test_new_activate_close_tab(mock):
    """A tab's complete lifecycle — creation on a URL, activation, closing —
    goes through the HTTP /json API and leaves the inventory consistent."""
    tab = discovery.new_tab("127.0.0.1", mock.http_port, "http://example.test/x")
    #: the tab is born on the requested URL and is added to the target
    #: inventory
    assert tab["url"] == "http://example.test/x"
    assert len(discovery.list_targets("127.0.0.1", mock.http_port)) == 2
    discovery.activate_tab("127.0.0.1", mock.http_port, tab["id"])
    discovery.close_tab("127.0.0.1", mock.http_port, tab["id"])
    #: closing actually removes the target: back to the initial state
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

    with pytest.raises(discovery.DiscoveryError, match="invalid JSON") as error:
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
    """pick_page resolves a target by exact id and refuses an unknown id
    instead of falling back to another page."""
    tid = next(iter(mock.targets))
    #: the requested id is resolved as-is, without substitution
    assert discovery.pick_page("127.0.0.1", mock.http_port, tid)["id"] == tid
    #: a missing target raises the dedicated discovery error rather than a
    #: silent fallback to an arbitrary page
    with pytest.raises(discovery.DiscoveryError):
        discovery.pick_page("127.0.0.1", mock.http_port, "NOPE")


# -- client -----------------------------------------------------------------


def test_send_and_result(mock, evidence_case):
    """A command/response round trip succeeds and the frame emitted on the
    wire is exactly the one requested: output AND protocol are proven."""
    with _connect(mock) as c:
        #: the (empty) response from the enabled domain comes back
        #: correlated to the call
        assert c.send("Page.enable") == {}
    #: on the wire side, the mock received a single command with no
    #: parameters — it's the protocol actually emitted that's being judged,
    #: not just the return value
    assert mock.commands_for("Page.enable") == [{}]

    if evidence_case is not None:
        # Proof of the emitted protocol: the mock's trace of Page.enable commands.
        evidence_case.attach_json(
            "Mock protocol trace (Page.enable)",
            {"Page.enable": mock.commands_for("Page.enable")},
        )


def test_connection_failure_is_wrapped_with_endpoint_context(monkeypatch):
    def fail_connect(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(client_module, "connect", fail_connect)

    with pytest.raises(CDPTransportError, match="CDP connection failed.*ws://x.test") as error:
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

    with pytest.raises(CDPTransportError, match="sending Page.enable") as error:
        client.send_nowait("Page.enable")

    assert isinstance(error.value.__cause__, OSError)


def test_send_nowait_allows_event_before_command_response(mock):
    """Sending without waiting lets an event that arrived before the
    command response be consumed — a necessary condition for network
    interception, where the Fetch event precedes the end of navigation."""
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
    #: the interception event is readable before the navigation response,
    #: which a blocking send would make impossible (interception deadlock)
    assert ev["method"] == "Fetch.requestPaused"
    #: the navigation command was nonetheless indeed emitted on the wire
    assert mock.commands_for("Page.navigate") == [{"url": "http://x.test/"}]


def test_wait_response_survives_event_consumption(mock):
    """A command's response stays correlated by id even when events are
    consumed between the send and wait_response: nothing is lost in the
    events/responses interleaving."""
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
        #: an intermediate event is consumed first, without destroying the
        #: response still pending
        assert c.next_event(timeout=1)["method"] == "Fetch.requestPaused"
        response = c.wait_response(command_id)
        #: the response retrieved afterward does carry the frame ids
        #: scripted for this exact navigation
        assert response["frameId"] == "FRAME1" and response["loaderId"] == "LOADER1"


def test_cdp_error_raised(mock):
    """A protocol error returned by the browser becomes a typed CDPError
    that keeps the original JSON-RPC code, never a silent empty result."""
    #: an unknown method triggers the exception dedicated to the protocol
    with _connect(mock) as c, pytest.raises(CDPError) as exc:
        c.send("Bogus.method")
    #: the "method not found" JSON-RPC code survives all the way to the
    #: diagnostic
    assert exc.value.code == -32601


def test_events_buffered_then_waited(mock):
    """Waiting for a specific event does not destroy those that arrived
    before it: the buffer allows consuming them afterward, in any order."""
    with _connect(mock) as c:
        c.send("Page.navigate", {"url": "http://x.test/"})
        ev = c.wait_event("Page.loadEventFired", timeout=2)
        #: the targeted wait skips over domContentEventFired and does find
        #: the load event scripted by the mock
        assert ev["params"]["timestamp"] == 1.2
        # domContentEventFired stayed in the buffer, consumable afterward
        ev2 = c.wait_event("Page.domContentEventFired", timeout=0.5)
        #: the earlier event, unclaimed on the first pass, is still
        #: available — proof that nothing was dropped along the way
        assert ev2["params"]["timestamp"] == 1.0


def test_wait_event_preserves_interleaved_command_response(mock):
    with _connect(mock) as client:
        command_id = client.send_nowait("Page.navigate", {"url": "http://x.test/"})

        event = client.wait_event("Page.loadEventFired", timeout=2)
        response = client.wait_response(command_id, timeout=0.1)

    assert event["method"] == "Page.loadEventFired"
    assert response == {"frameId": "FRAME1", "loaderId": "LOADER1"}


def test_wait_event_timeout(mock):
    """Waiting for an event that never comes fails within a bounded time
    via a dedicated exception: no possible CLI blocking."""
    #: without navigation, no load arrives: the short delay raises
    #: CDPTimeout instead of suspending the caller indefinitely
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
    """The windowed collection retains only the requested methods and
    drains the buffer along the way: no event leaks into the next command."""
    mock.script_console([{"type": "log", "args": [{"type": "string", "value": "x"}]}])
    with _connect(mock) as c:
        c.send("Runtime.enable")
        got = c.collect_events(0.3, ("Runtime.consoleAPICalled",))
        #: only the scripted console event crosses the method filter
        assert len(got) == 1
        #: the internal buffer comes out empty: the listening window drained
        #: everything
        assert c.events == []


def test_collect_events_preserves_interleaved_command_response(mock):
    with _connect(mock) as client:
        command_id = client.send_nowait("Page.enable")

        assert client.collect_events(0.05) == []
        assert client.wait_response(command_id, timeout=0) == {}


def test_collect_events_rejects_negative_duration_without_draining(mock):
    with _connect(mock) as client:
        client.events = [{"method": "Runtime.consoleAPICalled", "params": {}}]

        with pytest.raises(ValueError, match="collection duration"):
            client.collect_events(-0.1)

        assert len(client.events) == 1


@pytest.mark.parametrize(
    ("received", "message"),
    [
        (OSError("connection closed"), "transport interrupted"),
        ("not-json", "invalid CDP message"),
        ("[]", "CDP object required"),
    ],
)
def test_collect_events_surfaces_transport_and_frame_failures(received, message):
    """An interrupted collection is never presented as a partial success."""

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
        ('{"method":3,"params":{}}', "invalid CDP event"),
        (
            '{"method":"Page.loadEventFired","params":[]}',
            "invalid CDP params",
        ),
        ('{"id":"1","result":{}}', "CDP response without valid id"),
        ('{"id":1,"result":[]}', "invalid CDP result"),
        (
            '{"id":1,"error":{"code":"x","message":3}}',
            "malformed CDP error",
        ),
        (
            '{"id":1,"result":{},"error":{"code":1,"message":"x"}}',
            "ambiguous CDP response",
        ),
        (
            '{"method":"Page.loadEventFired","result":{}}',
            "invalid CDP event",
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
