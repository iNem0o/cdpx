"""Primitive réseau: observer ce que la page charge réellement.

Usecase agent (dev Symfony/e-commerce): repérer immédiatement les XHR en 500,
les assets 404, les appels API inattendus, le poids transféré — sans ouvrir
DevTools. C'est le pendant réseau de `console`: le feedback loop complet.
"""

from __future__ import annotations

from cdpx.client import CDPClient, validate_time_budget
from cdpx.primitives import nav
from cdpx.security import RedactionContext, redact_text, redact_url

NET_EVENTS = (
    "Network.requestWillBeSent",
    "Network.responseReceived",
    "Network.loadingFinished",
    "Network.loadingFailed",
)


def capture(
    client: CDPClient,
    url: str,
    timeout: float = 30.0,
    settle: float = 0.5,
    context: RedactionContext | None = None,
) -> dict:
    """Navigue vers `url` en capturant l'activité réseau jusqu'à load + settle."""
    timeout = validate_time_budget(timeout, "timeout réseau")
    settle = validate_time_budget(settle, "stabilisation réseau")
    redaction = context or RedactionContext()
    client.send("Network.enable")
    client.send("Page.enable")
    navigation = client.send("Page.navigate", {"url": url}, timeout=timeout)
    nav.raise_for_navigation_error(navigation, url, wait="load")
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
            request = p.get("request", {})
            request_url = request.get("url")
            entry["url"] = (
                redact_url(
                    request_url,
                    context=redaction,
                    path=f"$.requests.{rid}.url",
                )
                if isinstance(request_url, str)
                else request_url
            )
            entry["method"] = request.get("method")
            entry["resourceType"] = p.get("type")
        elif ev["method"] == "Network.responseReceived":
            resp = p.get("response", {})
            response_url = resp.get("url")
            if isinstance(response_url, str):
                entry["url"] = redact_url(
                    response_url,
                    context=redaction,
                    path=f"$.requests.{rid}.url",
                )
            entry["status"] = resp.get("status")
            entry["mimeType"] = resp.get("mimeType")
        elif ev["method"] == "Network.loadingFinished":
            entry["encodedBytes"] = p.get("encodedDataLength")
        elif ev["method"] == "Network.loadingFailed":
            error = p.get("errorText", "failed")
            entry["failed"] = (
                redact_text(error, context=redaction, path=f"$.requests.{rid}.failed")
                if isinstance(error, str)
                else error
            )

    reqs = list(requests.values())
    return {
        "url": redact_url(url, context=redaction, path="$.url"),
        "requests": reqs,
        "summary": {
            "total": len(reqs),
            "failed": sum(1 for r in reqs if r.get("failed")),
            "errors_4xx_5xx": sum(1 for r in reqs if (r.get("status") or 0) >= 400),
            "bytes": sum(r.get("encodedBytes") or 0 for r in reqs),
        },
    }
