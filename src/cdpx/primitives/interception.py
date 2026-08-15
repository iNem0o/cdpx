"""Bounded network interception around a composed browser action."""

from __future__ import annotations

import base64
import fnmatch
import json
import time
from collections.abc import Callable
from typing import Any

from cdpx.cdp_types import CDPEvent
from cdpx.client import CDPClient, CDPTimeout, validate_time_budget
from cdpx.primitives import inputs, nav


def intercept_goto(
    client: CDPClient,
    url: str,
    *,
    rules: list[str],
    timeout: float = 30.0,
    settle: float = 0.5,
) -> dict[str, Any]:
    timeout = validate_time_budget(timeout, "interception timeout")
    settle = validate_time_budget(settle, "interception settle")
    parsed_rules = [parse_intercept_rule(rule) for rule in rules]
    started = time.monotonic()
    deadline = started + timeout

    def remaining() -> float:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise CDPTimeout(f"interception timeout after {timeout}s")
        return budget

    hits: list[dict[str, str]] = []
    enabled = False
    primary_error: Exception | None = None
    try:
        _enable_fetch(client, timeout=remaining())
        enabled = True
        client.send("Page.enable", timeout=remaining())
        remaining()
        navigation_id = client.send_nowait("Page.navigate", {"url": url})
        _collect_until_quiet(
            client,
            parsed_rules,
            hits,
            settle=settle,
            remaining=remaining,
            wait_for_load=True,
        )
        navigation = client.wait_response(
            navigation_id,
            timeout=remaining(),
        )
        nav.raise_for_navigation_error(navigation, url, wait="load")
        return {"url": url, "rules": rules, "hits": hits, "count": len(hits), "settle": settle}
    except Exception as error:
        primary_error = error
        raise
    finally:
        if enabled:
            _disable_fetch(client, primary_error)


def intercept_click(
    client: CDPClient,
    selector: str,
    *,
    rules: list[str],
    timeout: float = 30.0,
    settle: float = 0.5,
) -> dict[str, Any]:
    """Intercept requests triggered by the regular trusted click primitive."""
    timeout = validate_time_budget(timeout, "interception timeout")
    settle = validate_time_budget(settle, "interception settle")
    parsed_rules = [parse_intercept_rule(rule) for rule in rules]
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise CDPTimeout(f"interception timeout after {timeout}s")
        return budget

    hits: list[dict[str, str]] = []
    matched_count = 0
    effective_count = 0
    enabled = False
    primary_error: Exception | None = None
    try:
        _enable_fetch(client, timeout=remaining())
        enabled = True
        action_result = inputs.click(client, selector)
        matched_count, effective_count = _collect_until_quiet(
            client,
            parsed_rules,
            hits,
            settle=settle,
            remaining=remaining,
            wait_for_load=False,
        )
        return {
            "action": {
                "argv": ["click", selector],
                "result": action_result,
            },
            "rules": rules,
            "hits": hits,
            "count": len(hits),
            "matched_count": matched_count,
            "effective_count": effective_count,
            "settle": settle,
        }
    except Exception as error:
        primary_error = error
        raise
    finally:
        if enabled:
            _disable_fetch(client, primary_error)


def _enable_fetch(client: CDPClient, *, timeout: float) -> None:
    client.send(
        "Fetch.enable",
        {"patterns": [{"urlPattern": "*"}]},
        timeout=timeout,
    )


def _disable_fetch(client: CDPClient, primary_error: Exception | None) -> None:
    try:
        # Cleanup gets its own bounded attempt even when the action exhausted
        # its execution budget. Closing the client remains the transport fallback.
        client.send("Fetch.disable", timeout=client.timeout)
    except Exception as cleanup_error:
        if primary_error is None:
            raise
        primary_error.add_note(f"interception cleanup failed: {cleanup_error}")


def _collect_until_quiet(
    client: CDPClient,
    rules: list[dict[str, str]],
    hits: list[dict[str, str]],
    *,
    settle: float,
    remaining: Callable[[], float],
    wait_for_load: bool,
) -> tuple[int, int]:
    last_event = time.monotonic()
    load_seen = not wait_for_load
    matched_count = 0
    effective_count = 0
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
        matched, effective = _resolve_paused_request(client, rules, event, hits)
        matched_count += int(matched)
        effective_count += int(effective)
    return matched_count, effective_count


def _resolve_paused_request(
    client: CDPClient,
    rules: list[dict[str, str]],
    event: CDPEvent,
    hits: list[dict[str, str]],
) -> tuple[bool, bool]:
    params = event.get("params", {})
    request = params.get("request", {})
    request_url = request.get("url", "")
    rule = _match_rule(rules, request_url)
    action = rule["action"] if rule else "continue"
    if action == "continue":
        client.send("Fetch.continueRequest", {"requestId": params["requestId"]})
    elif action == "block":
        client.send(
            "Fetch.failRequest",
            {"requestId": params["requestId"], "errorReason": "BlockedByClient"},
        )
    elif action.isascii() and len(action) == 3 and action.isdigit() and 200 <= int(action) <= 599:
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
        raise AssertionError(f"unvalidated interception action: {action}")
    hits.append({"url": request_url, "action": action})
    matched = rule is not None
    return matched, matched and action != "continue"


def parse_intercept_rule(rule: str) -> dict[str, str]:
    if "=>" not in rule:
        raise ValueError("expected rule: PATTERN => ACTION")
    pattern, action = [part.strip() for part in rule.split("=>", 1)]
    if not pattern:
        raise ValueError("empty interception pattern")
    if action not in {"continue", "block"}:
        is_status = action.isascii() and len(action) == 3 and action.isdigit()
        if not is_status or not 200 <= int(action) <= 599:
            raise ValueError("expected interception action: continue, block, or status 200..599")
    return {"pattern": pattern, "action": action}


def _match_rule(rules: list[dict[str, str]], url: str) -> dict[str, str] | None:
    for rule in rules:
        pattern = rule["pattern"]
        if fnmatch.fnmatch(url, pattern) or pattern in url:
            return rule
    return None
