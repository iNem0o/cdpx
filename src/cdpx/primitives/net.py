"""Primitive réseau: observer ce que la page charge réellement.

Usecase agent (dev Symfony/e-commerce): repérer immédiatement les XHR en 500,
les assets 404, les appels API inattendus, le poids transféré — sans ouvrir
DevTools. C'est le pendant réseau de `console`: le feedback loop complet.
"""

from __future__ import annotations

from cdpx.client import CDPClient

NET_EVENTS = (
    "Network.requestWillBeSent",
    "Network.responseReceived",
    "Network.loadingFinished",
    "Network.loadingFailed",
)


def capture(client: CDPClient, url: str, timeout: float = 30.0, settle: float = 0.5) -> dict:
    """Navigue vers `url` en capturant l'activité réseau jusqu'à load + settle."""
    client.send("Network.enable")
    client.send("Page.enable")
    navigation = client.send("Page.navigate", {"url": url}, timeout=timeout)
    if navigation.get("errorText"):
        raise ValueError(f"navigation échouée: {navigation['errorText']}")
    client.wait_event("Page.loadEventFired", timeout=timeout)
    events = client.collect_events(settle, NET_EVENTS)

    requests: dict[str, dict] = {}
    for ev in events:
        p = ev.get("params", {})
        rid = p.get("requestId")
        if not rid:
            continue
        entry = requests.setdefault(rid, {"requestId": rid})
        if ev["method"] == "Network.requestWillBeSent":
            entry["url"] = p.get("request", {}).get("url")
            entry["method"] = p.get("request", {}).get("method")
            entry["resourceType"] = p.get("type")
        elif ev["method"] == "Network.responseReceived":
            resp = p.get("response", {})
            entry["status"] = resp.get("status")
            entry["mimeType"] = resp.get("mimeType")
        elif ev["method"] == "Network.loadingFinished":
            entry["encodedBytes"] = p.get("encodedDataLength")
        elif ev["method"] == "Network.loadingFailed":
            entry["failed"] = p.get("errorText", "failed")

    reqs = list(requests.values())
    return {
        "url": url,
        "requests": reqs,
        "summary": {
            "total": len(reqs),
            "failed": sum(1 for r in reqs if r.get("failed")),
            "errors_4xx_5xx": sum(1 for r in reqs if (r.get("status") or 0) >= 400),
            "bytes": sum(r.get("encodedBytes") or 0 for r in reqs),
        },
    }
