"""Interception réseau bornée autour d'une navigation CDP."""

from __future__ import annotations

import base64
import fnmatch
import json
import time
from typing import Any

from cdpx.client import CDPClient, CDPTimeout, validate_time_budget
from cdpx.primitives import nav


def intercept_goto(
    client: CDPClient,
    url: str,
    *,
    rules: list[str],
    timeout: float = 30.0,
    settle: float = 0.5,
) -> dict[str, Any]:
    timeout = validate_time_budget(timeout, "timeout interception")
    settle = validate_time_budget(settle, "stabilisation interception")
    parsed_rules = [parse_intercept_rule(rule) for rule in rules]
    started = time.monotonic()
    deadline = started + timeout

    def remaining() -> float:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise CDPTimeout(f"timeout interception après {timeout}s")
        return budget

    client.send(
        "Fetch.enable",
        {"patterns": [{"urlPattern": "*"}]},
        timeout=remaining(),
    )
    client.send("Page.enable", timeout=remaining())
    remaining()
    navigation_id = client.send_nowait("Page.navigate", {"url": url})

    last_event = time.monotonic()
    load_seen = False
    hits: list[dict[str, str]] = []
    while True:
        remaining_budget = remaining()
        if load_seen and time.monotonic() - last_event >= settle:
            break
        try:
            event = client.next_event(timeout=min(0.25, remaining_budget))
        except CDPTimeout:
            continue
        last_event = time.monotonic()
        if event["method"] == "Page.loadEventFired":
            load_seen = True
            continue
        if event["method"] != "Fetch.requestPaused":
            continue
        params = event.get("params", {})
        request = params.get("request", {})
        request_url = request.get("url", "")
        rule = _match_rule(parsed_rules, request_url)
        action = rule["action"] if rule else "continue"
        if action == "continue":
            client.send("Fetch.continueRequest", {"requestId": params["requestId"]})
        elif action == "block":
            client.send(
                "Fetch.failRequest",
                {"requestId": params["requestId"], "errorReason": "BlockedByClient"},
            )
        elif (
            action.isascii() and len(action) == 3 and action.isdigit() and 200 <= int(action) <= 599
        ):
            status = int(action)
            body = json.dumps({"cdpx": "intercept", "status": status}).encode()
            client.send(
                "Fetch.fulfillRequest",
                {
                    "requestId": params["requestId"],
                    "responseCode": status,
                    "responseHeaders": [{"name": "Content-Type", "value": "application/json"}],
                    "body": base64.b64encode(body).decode(),
                },
            )
        else:  # pragma: no cover - parse_intercept_rule validates the domain.
            raise AssertionError(f"action d'interception non validée: {action}")
        hits.append({"url": request_url, "action": action})
    navigation = client.wait_response(
        navigation_id,
        timeout=remaining(),
    )
    nav.raise_for_navigation_error(navigation, url, wait="load")
    return {"url": url, "rules": rules, "hits": hits, "count": len(hits), "settle": settle}


def parse_intercept_rule(rule: str) -> dict[str, str]:
    if "=>" not in rule:
        raise ValueError("règle attendue: PATTERN => ACTION")
    pattern, action = [part.strip() for part in rule.split("=>", 1)]
    if not pattern:
        raise ValueError("motif d'interception vide")
    if action not in {"continue", "block"}:
        is_status = action.isascii() and len(action) == 3 and action.isdigit()
        if not is_status or not 200 <= int(action) <= 599:
            raise ValueError("action d'interception attendue: continue, block ou statut 200..599")
    return {"pattern": pattern, "action": action}


def _match_rule(rules: list[dict[str, str]], url: str) -> dict[str, str] | None:
    for rule in rules:
        pattern = rule["pattern"]
        if fnmatch.fnmatch(url, pattern) or pattern in url:
            return rule
    return None
