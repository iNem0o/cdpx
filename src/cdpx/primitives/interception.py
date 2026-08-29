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
from cdpx.policy import PolicyError, assert_url_allowed
from cdpx.primitives import inputs, nav

_CLEANUP_QUIET_SECONDS = 0.25
_CLEANUP_POST_DISABLE_SECONDS = 0.05
_CLEANUP_MAX_SECONDS = 2.0


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
    allowed_origins: tuple[str, ...],
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
        main_frame_id = _main_frame_id(client, timeout=remaining())
        _enable_fetch(client, timeout=remaining())
        enabled = True
        action_result = inputs.click(client, selector, remaining=remaining)
        matched_count, effective_count = _collect_until_quiet(
            client,
            parsed_rules,
            hits,
            settle=settle,
            remaining=remaining,
            wait_for_load=False,
            main_frame_id=main_frame_id,
            allowed_origins=allowed_origins,
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


def _main_frame_id(client: CDPClient, *, timeout: float) -> str:
    result = client.send("Page.getFrameTree", timeout=timeout)
    frame_id = result.get("frameTree", {}).get("frame", {}).get("id")
    if not isinstance(frame_id, str) or not frame_id:
        raise ValueError("unable to determine the main frame")
    return frame_id


def _disable_fetch(client: CDPClient, primary_error: Exception | None) -> None:
    deadline = time.monotonic() + min(client.timeout, _CLEANUP_MAX_SECONDS)

    def remaining() -> float:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise CDPTimeout("interception cleanup timeout")
        return budget

    try:
        while True:
            _release_buffered_paused_requests(client, remaining=remaining, wait_for_late=True)
            remaining()
            # Cleanup gets its own bounded attempt even when the action exhausted
            # its execution budget. Closing the client remains the transport fallback.
            client.send("Fetch.disable", timeout=client.timeout)
            late_pauses = client.collect_events(
                min(_CLEANUP_POST_DISABLE_SECONDS, remaining()),
                ("Fetch.requestPaused",),
            )
            if not late_pauses:
                return
            # A pause can race with the disable response. Re-enable the domain
            # before deciding that request, then repeat until one disable cycle
            # observes a quiet wire.
            _enable_fetch(client, timeout=remaining())
            _continue_paused_requests(client, late_pauses, remaining=remaining)
    except Exception as cleanup_error:
        if primary_error is not None:
            primary_error.add_note(f"interception cleanup failed: {cleanup_error}")
            return
        raise cleanup_error


def _release_buffered_paused_requests(
    client: CDPClient,
    *,
    remaining: Callable[[], float],
    wait_for_late: bool,
) -> None:
    """Continue pauses observed after the rule-processing snapshot."""
    while True:
        paused = client.drain_events(("Fetch.requestPaused",))
        if not paused and wait_for_late:
            paused = client.collect_events(
                min(_CLEANUP_QUIET_SECONDS, remaining()),
                ("Fetch.requestPaused",),
            )
        if not paused:
            return
        _continue_paused_requests(client, paused, remaining=remaining)


def _continue_paused_requests(
    client: CDPClient,
    paused: list[CDPEvent],
    *,
    remaining: Callable[[], float],
) -> None:
    for event in paused:
        request_id = event.get("params", {}).get("requestId")
        if not isinstance(request_id, str) or not request_id:
            continue
        client.send(
            "Fetch.continueRequest",
            {"requestId": request_id},
            timeout=remaining(),
        )


def _collect_until_quiet(
    client: CDPClient,
    rules: list[dict[str, str]],
    hits: list[dict[str, str]],
    *,
    settle: float,
    remaining: Callable[[], float],
    wait_for_load: bool,
    main_frame_id: str | None = None,
    allowed_origins: tuple[str, ...] | None = None,
) -> tuple[int, int]:
    last_event = time.monotonic()
    load_seen = not wait_for_load
    matched_count = 0
    effective_count = 0

    def process(event: CDPEvent) -> None:
        nonlocal effective_count, last_event, load_seen, matched_count
        last_event = time.monotonic()
        if event["method"] == "Page.loadEventFired":
            load_seen = True
            return
        if event["method"] != "Fetch.requestPaused":
            return
        _guard_main_document_origin(
            client,
            event,
            main_frame_id=main_frame_id,
            allowed_origins=allowed_origins,
            remaining=remaining,
        )
        matched, effective = _resolve_paused_request(
            client,
            rules,
            event,
            hits,
            remaining=remaining,
        )
        matched_count += int(matched)
        effective_count += int(effective)

    if load_seen and settle == 0:
        # Freeze exactly the events buffered by the completed click. Resolving
        # this snapshot uses synchronous CDP commands that may buffer newer
        # traffic; cleanup continues that traffic without applying rules.
        remaining()
        snapshot = client.drain_events()
        for event in snapshot:
            process(event)
        return matched_count, effective_count

    while True:
        remaining_budget = remaining()
        buffered = client.drain_events()
        if buffered:
            for event in buffered:
                process(event)
            continue

        poll_timeout = min(0.25, remaining_budget)
        if load_seen:
            quiet_remaining = settle - (time.monotonic() - last_event)
            if quiet_remaining <= 0:
                break
            poll_timeout = min(poll_timeout, quiet_remaining)
        try:
            event = client.next_event(timeout=poll_timeout)
        except CDPTimeout:
            continue
        process(event)
    return matched_count, effective_count


def _guard_main_document_origin(
    client: CDPClient,
    event: CDPEvent,
    *,
    main_frame_id: str | None,
    allowed_origins: tuple[str, ...] | None,
    remaining: Callable[[], float],
) -> None:
    if main_frame_id is None or allowed_origins is None:
        return
    params = event.get("params", {})
    if params.get("frameId") != main_frame_id or params.get("resourceType") != "Document":
        return
    request_url = params.get("request", {}).get("url", "")
    try:
        assert_url_allowed(request_url, allowed_origins)
    except PolicyError as policy_error:
        try:
            # Let the browser's navigation proceed untouched. The command
            # fails, but interception never mutates the forbidden document.
            client.send(
                "Fetch.continueRequest",
                {"requestId": params["requestId"]},
                timeout=remaining(),
            )
        except Exception as release_error:
            policy_error.add_note(f"forbidden document release failed: {release_error}")
        raise


def _resolve_paused_request(
    client: CDPClient,
    rules: list[dict[str, str]],
    event: CDPEvent,
    hits: list[dict[str, str]],
    *,
    remaining: Callable[[], float],
) -> tuple[bool, bool]:
    params = event.get("params", {})
    request = params.get("request", {})
    request_url = request.get("url", "")
    rule = _match_rule(rules, request_url)
    action = rule["action"] if rule else "continue"
    if action == "continue":
        client.send(
            "Fetch.continueRequest",
            {"requestId": params["requestId"]},
            timeout=remaining(),
        )
    elif action == "block":
        client.send(
            "Fetch.failRequest",
            {"requestId": params["requestId"], "errorReason": "BlockedByClient"},
            timeout=remaining(),
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
            timeout=remaining(),
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
