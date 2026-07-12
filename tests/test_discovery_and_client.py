"""Découverte HTTP (/json) et client WebSocket, validés contre le mock CDP."""

import pytest

from cdpx import discovery
from cdpx.client import CDPClient, CDPError, CDPTimeout


def _connect(mock) -> CDPClient:
    target_id = next(iter(mock.targets))
    target = discovery.pick_page("127.0.0.1", mock.http_port, target_id)
    return CDPClient(target["webSocketDebuggerUrl"], timeout=5)


# -- découverte ------------------------------------------------------------------


def test_list_targets(mock):
    targets = discovery.list_targets("127.0.0.1", mock.http_port)
    assert len(targets) == 1
    assert targets[0]["type"] == "page"
    assert targets[0]["webSocketDebuggerUrl"].startswith("ws://127.0.0.1:")


def test_version(mock):
    v = discovery.version("127.0.0.1", mock.http_port)
    assert v["Protocol-Version"] == "1.3"


def test_loopback_discovery_ignores_environment_proxy(mock, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    assert discovery.version("127.0.0.1", mock.http_port)["Protocol-Version"] == "1.3"


def test_new_activate_close_tab(mock):
    tab = discovery.new_tab("127.0.0.1", mock.http_port, "http://example.test/x")
    assert tab["url"] == "http://example.test/x"
    assert len(discovery.list_targets("127.0.0.1", mock.http_port)) == 2
    discovery.activate_tab("127.0.0.1", mock.http_port, tab["id"])
    discovery.close_tab("127.0.0.1", mock.http_port, tab["id"])
    assert len(discovery.list_targets("127.0.0.1", mock.http_port)) == 1


def test_pick_page_by_id_and_missing(mock):
    tid = next(iter(mock.targets))
    assert discovery.pick_page("127.0.0.1", mock.http_port, tid)["id"] == tid
    with pytest.raises(discovery.DiscoveryError):
        discovery.pick_page("127.0.0.1", mock.http_port, "NOPE")


# -- client ---------------------------------------------------------------------


def test_send_and_result(mock):
    with _connect(mock) as c:
        assert c.send("Page.enable") == {}
    assert mock.commands_for("Page.enable") == [{}]


def test_send_nowait_allows_event_before_command_response(mock):
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
    assert ev["method"] == "Fetch.requestPaused"
    assert mock.commands_for("Page.navigate") == [{"url": "http://x.test/"}]


def test_wait_response_survives_event_consumption(mock):
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
        assert c.next_event(timeout=1)["method"] == "Fetch.requestPaused"
        response = c.wait_response(command_id)
        assert response["frameId"] == "FRAME1" and response["loaderId"] == "LOADER1"


def test_cdp_error_raised(mock):
    with _connect(mock) as c, pytest.raises(CDPError) as exc:
        c.send("Bogus.method")
    assert exc.value.code == -32601


def test_events_buffered_then_waited(mock):
    with _connect(mock) as c:
        c.send("Page.navigate", {"url": "http://x.test/"})
        ev = c.wait_event("Page.loadEventFired", timeout=2)
        assert ev["params"]["timestamp"] == 1.2
        # domContentEventFired est resté dans le buffer, consommable après coup
        ev2 = c.wait_event("Page.domContentEventFired", timeout=0.5)
        assert ev2["params"]["timestamp"] == 1.0


def test_wait_event_timeout(mock):
    with _connect(mock) as c, pytest.raises(CDPTimeout):
        c.wait_event("Page.loadEventFired", timeout=0.3)


def test_collect_events_filters_and_drains(mock):
    mock.script_console([{"type": "log", "args": [{"type": "string", "value": "x"}]}])
    with _connect(mock) as c:
        c.send("Runtime.enable")
        got = c.collect_events(0.3, ("Runtime.consoleAPICalled",))
        assert len(got) == 1
        assert c.events == []
