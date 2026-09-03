"""Browser diagnostics: Web Vitals, accessibility, and coverage."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from cdpx.client import (
    CDPClient,
    CDPError,
    CDPTimeout,
    CDPTransportError,
    validate_time_budget,
)
from cdpx.policy import assert_url_allowed, parse_origins
from cdpx.primitives import actions, inputs, js, nav

VITALS_SCHEMA = "cdpx.vitals/v3"
VITALS_COLLECTOR_VERSION = 3
VITALS_WORLD_NAME = "cdpx-vitals"
MAX_CLS_ENTRIES = 50
MAX_CLS_SOURCES_PER_ENTRY = 5
MAX_CLS_CLASSES_PER_NODE = 5
MAX_SNAPSHOT_ERRORS = 10

#: How the measured document received its collector. "document-start" means
#: the collector was evaluated before any page script (registration for
#: future documents or a fresh document); "capture-time" means it was armed
#: after the document started, so only what the browser's bounded buffers
#: still hold can be replayed.
VITALS_ARM_SCOPES = ("document-start", "capture-time")

#: How the measured document was reached. Reported in `document.navigation_source`.
VITALS_NAVIGATION_SOURCES = ("goto", "click", "redirect", "current-document")

#: Physical bounds agreed for a bounded laboratory measurement. A snapshot
#: exceeding them is refused (status "unavailable") instead of being silently
#: clamped: layout-shift values are viewport-relative fractions, times are
#: relative to the document time origin and INP durations are milliseconds.
MAX_ENTRY_VALUE = 100.0
MAX_WINDOW_VALUE = 10_000.0
MAX_RAW_SUM = 100_000.0
MAX_TIME_MS = 100_000_000.0
MAX_DURATION_MS = 1_000_000.0
MAX_COORDINATE = 10_000_000.0
MAX_ENTRY_COUNT = 1_000_000
#: `performance.timeOrigin` is an epoch milliseconds timestamp.
MAX_TIME_ORIGIN_MS = 1_000_000_000_000_0.0

VITALS_COLLECTOR_SOURCE = f"""
(() => {{
  // cdpx vitals collector. It runs inside a CDP isolated world created for
  // the main frame: application JavaScript cannot see, touch or falsify it.
  // When cdpx registers it through Page.addScriptToEvaluateOnNewDocument it
  // also runs at the start of every following main-frame document until the
  // registration is removed, so a journey never measures a document whose
  // collector was armed late.
  if (window.top !== window) return; // main frame only
  if (globalThis.__cdpxVitalsRead) return;

  const MAX_ENTRIES = {MAX_CLS_ENTRIES};
  const MAX_SOURCES = {MAX_CLS_SOURCES_PER_ENTRY};
  const MAX_CLASSES = {MAX_CLS_CLASSES_PER_NODE};
  const number = (value) => Number.isFinite(value) ? value : 0;
  const rect = (value) => value ? {{
    x: number(value.x),
    y: number(value.y),
    width: number(value.width),
    height: number(value.height)
  }} : null;
  const node = (value) => {{
    if (!value || value.nodeType !== Node.ELEMENT_NODE) return null;
    const tag = String(value.tagName || '').toLowerCase().slice(0, 32);
    const id = String(value.id || '').slice(0, 120);
    const classes = Array.from(value.classList || [])
      .slice(0, MAX_CLASSES)
      .map((item) => String(item).slice(0, 80));
    const selector = (id ? `#${{CSS.escape(id)}}` : [tag, ...classes.map(
      (item) => `.${{CSS.escape(item)}}`
    )].join('')).slice(0, 240);
    return {{tag, id, classes, selector}};
  }};
  const source = (value) => ({{
    node: node(value && value.node),
    previous_rect: rect(value && value.previousRect),
    current_rect: rect(value && value.currentRect)
  }});
  const shapeEntry = (entry) => {{
    const sources = Array.from(entry.sources || []);
    return {{
      value: number(entry.value),
      start_time: number(entry.startTime),
      duration: number(entry.duration),
      had_recent_input: Boolean(entry.hadRecentInput),
      sources: sources.slice(0, MAX_SOURCES).map(source),
      source_count: sources.length,
      sources_truncated: sources.length > MAX_SOURCES
    }};
  }};

  const state = {{
    schema: '{VITALS_SCHEMA}',
    collector_version: {VITALS_COLLECTOR_VERSION},
    document_observed: true,
    // "document-start" only when this evaluation happened before the document
    // finished parsing. A late arm replays what the browser's bounded
    // performance buffers still hold — never a guaranteed complete history.
    arm_scope: document.readyState === 'loading' ? 'document-start' : 'capture-time',
    supported: {{lcp: false, layout_shift: false, event_timing: false}},
    errors: [],
    dropped_entries: 0,
    interaction_entry_count: 0,
    lcp: 0,
    cls: 0,
    raw_sum: 0,
    inp: 0,
    total_entries: 0,
    ignored_recent_input: 0,
    winning_window: null
  }};
  let current = null;
  const observers = [];

  // Official CLS session-window reducer, kept pure so the exact shipped
  // logic can be executed against synthetic entries in tests. A new window
  // starts when an entry is 1000 ms or more after the previous eligible
  // entry, or 5000 ms or more after the window's first entry. Entries with
  // `hadRecentInput` never join a window and never extend one. Equal window
  // values keep the first window.
  const reduceLayoutShift = (entry) => {{
    if (!entry || typeof entry !== 'object') return;
    const value = Number(entry.value);
    const start = Number(entry.startTime);
    if (!Number.isFinite(value) || value < 0 || !Number.isFinite(start) || start < 0) return;
    if (entry.hadRecentInput) {{
      state.ignored_recent_input += 1;
      return;
    }}
    state.raw_sum += value;
    state.total_entries += 1;
    if (
      current &&
      start - current.last_time < 1000 &&
      start - current.start_time < 5000
    ) {{
      current.value += value;
      current.last_time = start;
    }} else {{
      current = {{
        value,
        start_time: start,
        last_time: start,
        entry_count: 0,
        entries: []
      }};
    }}
    current.entry_count += 1;
    if (current.entries.length < MAX_ENTRIES) current.entries.push(shapeEntry(entry));
    if (!state.winning_window || current.value > state.winning_window.value) {{
      state.winning_window = current;
    }}
    state.cls = state.winning_window.value;
  }};

  const registerObserver = (key, type, options, handler) => {{
    // An entry type the browser does not implement is silently ignored by
    // observe() instead of raising; supportedEntryTypes is the documented
    // detection method and keeps an unsupported signal from masquerading
    // as a measured zero.
    const supportedTypes = PerformanceObserver.supportedEntryTypes;
    if (!supportedTypes || !supportedTypes.includes(type)) {{
      state.supported[key] = false;
      return;
    }}
    try {{
      const observer = new PerformanceObserver((list, _observer, callbackOptions) => {{
        // The buffer announces abandoned entries on the first callback only;
        // a report backed by a lossy buffer must not claim a full measure.
        if (callbackOptions && Number.isFinite(callbackOptions.droppedEntriesCount)) {{
          state.dropped_entries += callbackOptions.droppedEntriesCount;
        }}
        for (const entry of list.getEntries()) handler(entry);
      }});
      observer.observe(Object.assign({{type, buffered: true}}, options));
      observers.push({{observer, handler}});
      state.supported[key] = true;
    }} catch (error) {{
      state.errors.push(String((error && error.message) || error).slice(0, 200));
    }}
  }};

  registerObserver('lcp', 'largest-contentful-paint', {{}}, (entry) => {{
    const start = Number(entry.startTime);
    if (Number.isFinite(start) && start >= 0 && start > state.lcp) state.lcp = start;
  }});
  registerObserver('layout_shift', 'layout-shift', {{}}, reduceLayoutShift);
  // Approximate interaction signal: the longest click entry duration. This
  // is NOT the official INP (no interactionId grouping, no p98 estimation,
  // keyboard and pointer events excluded); it is documented as an
  // approximation and must be read as such. The spec floors the effective
  // durationThreshold at 16 ms, so shorter interactions are never exposed.
  registerObserver('event_timing', 'event', {{durationThreshold: 0}}, (entry) => {{
    if (entry.name === 'click') {{
      state.interaction_entry_count += 1;
      const duration = Number(entry.duration);
      if (Number.isFinite(duration) && duration >= 0 && duration > state.inp) state.inp = duration;
    }}
  }});

  // Pending PerformanceObserver records are drained synchronously before a
  // snapshot so a capture with settle=0 never reads a stale state.
  const flushObservers = () => {{
    for (const {{observer, handler}} of observers) {{
      for (const entry of observer.takeRecords()) handler(entry);
    }}
  }};

  const snapshotWindow = (window) => window ? {{
    value: window.value,
    start_time: window.start_time,
    end_time: window.last_time,
    duration: Math.max(0, window.last_time - window.start_time),
    entry_count: window.entry_count,
    entries: window.entries.slice(),
    entries_truncated: window.entry_count > window.entries.length
  }} : null;

  globalThis.__cdpxVitalsRead = () => {{
    flushObservers();
    const navigation = performance.getEntriesByType('navigation')[0];
    return {{
      schema: state.schema,
      collector_version: state.collector_version,
      document_observed: state.document_observed,
      arm_scope: state.arm_scope,
      supported: Object.assign({{}}, state.supported),
      errors: state.errors.slice(0, {MAX_SNAPSHOT_ERRORS}),
      dropped_entries: state.dropped_entries,
      interaction_entry_count: state.interaction_entry_count,
      metrics: {{
        lcp: state.lcp,
        cls: state.cls,
        raw_sum: state.raw_sum,
        inp: state.inp,
        total_entries: state.total_entries,
        ignored_recent_input: state.ignored_recent_input,
        winning_window: snapshotWindow(state.winning_window)
      }},
      context: {{
        navigation_type: navigation ? String(navigation.type) : null,
        // Read atomically with the metrics: the binding can never describe
        // a different document than the one the numbers came from.
        document_url: String(location.href),
        time_origin: performance.timeOrigin,
        viewport: {{
          width: innerWidth,
          height: innerHeight,
          dpr: devicePixelRatio
        }}
      }}
    }};
  }};

  // Diagnostic/test hook: the pure session-window reducer, reachable only
  // from this isolated world (application JavaScript cannot see it), so the
  // exact shipped aggregation logic can be executed against synthetic
  // entries at the boundary values.
  globalThis.__cdpxVitalsReduce = reduceLayoutShift;
}})();
"""


def install_vitals_observer(
    client: CDPClient,
    *,
    for_future_documents: bool = False,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Arm the vitals collector inside a CDP isolated world.

    The collector lives in an isolated world attached to the main frame:
    application JavaScript cannot see, reassign or falsify it. With
    ``for_future_documents`` it is additionally registered through
    ``Page.addScriptToEvaluateOnNewDocument`` so every main-frame document
    navigated afterwards is instrumented from its first script — a journey
    never measures a document whose collector was armed late. That
    registration MUST be released with :func:`release_vitals_collector`;
    without it nothing is registered for future documents and no
    instrumentation can leak into subsequent navigations.

    Returns a handle that :func:`collect_vitals` reuses across captures of
    the same document and re-arms automatically after a navigation destroyed
    the previous execution context.
    """
    handle: dict[str, Any] = {}
    client.send("Page.enable", timeout=timeout)
    if for_future_documents:
        registered = client.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": VITALS_COLLECTOR_SOURCE, "worldName": VITALS_WORLD_NAME},
            timeout=timeout,
        )
        script_id = registered.get("identifier")
        if isinstance(script_id, bool) or not isinstance(script_id, str) or not script_id:
            raise ValueError("vitals: Chrome did not register the new-document collector")
        handle["script_id"] = script_id
    frame_id = _main_frame_id(client, timeout=timeout)
    handle["frame_id"] = frame_id
    handle["context_id"] = _arm_collector(client, frame_id, timeout=timeout)
    return handle


def release_vitals_collector(
    client: CDPClient,
    handle: dict[str, Any] | None,
    *,
    timeout: float | None = None,
) -> None:
    """Remove the new-document registration armed by :func:`install_vitals_observer`.

    Idempotent: a handle without a registration (capture-time arm) is simply
    cleared. A failed removal raises — an instrumentation that would survive
    into unrelated documents must never be silenced.
    """
    if not isinstance(handle, dict):
        return
    script_id = handle.pop("script_id", None)
    handle.pop("frame_id", None)
    handle.pop("context_id", None)
    if isinstance(script_id, str) and script_id:
        client.send(
            "Page.removeScriptToEvaluateOnNewDocument",
            {"identifier": script_id},
            timeout=timeout,
        )


def collect_vitals(
    client: CDPClient,
    settle: float = 0.5,
    handle: dict[str, Any] | None = None,
    *,
    requested_url: str | None = None,
    browser_version: str | None = None,
    interaction_requested: bool = False,
    allowed_origins: tuple[str, ...] | None = None,
    navigation_source: str | None = None,
    navigation_step: str | None = None,
    pre_interaction_url: str | None = None,
    origin_guard: Callable[[], object] | None = None,
    remaining: Callable[[], float] | None = None,
    measurement_environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the current document's vitals snapshot from the isolated world.

    The settle wait buffers incoming CDP events without consuming them, so
    passive console/network collectors never lose late events to a vitals
    capture. Published contract: a CDP, transport or JavaScript failure
    while arming or reading the collector propagates to the caller (the
    command fails loudly); a snapshot that IS obtained but is incoherent or
    tampered with reports ``status: "unavailable"`` with a reason and
    ``metrics: null`` instead of a silent zero; a browser that announces
    dropped performance entries degrades the report to
    ``status: "partial"`` with its metrics attached.

    ``allowed_origins`` judges the snapshot's own document binding before
    the result is returned, and ``origin_guard`` is invoked before every
    (re-)arm so a document outside the run's policy is never touched.
    """
    settle = validate_time_budget(settle, "vitals settle")
    client.settle(settle)
    handle = handle if handle is not None else {}
    context_id = handle.get("context_id")
    snapshot: dict[str, Any] | None = None
    if context_id is not None:
        try:
            snapshot = _read_snapshot(client, context_id, timeout=_budget(remaining))
        except js.JSException, CDPError, CDPTransportError:
            # The execution context died with a previous document: re-arm in
            # the current one. A collector registered for future documents
            # was already re-installed at that document's start.
            context_id = None
    if snapshot is None:
        frame_id = handle.get("frame_id")
        if not isinstance(frame_id, str) or not frame_id:
            frame_id = _main_frame_id(client, timeout=_budget(remaining))
            handle["frame_id"] = frame_id
        last_error: Exception | None = None
        for attempt in range(2):
            if origin_guard is not None:
                origin_guard()
            if attempt > 0:
                # A navigation may have replaced the frame between the two
                # attempts: re-resolve the main frame once, then give up.
                frame_id = _main_frame_id(client, timeout=_budget(remaining))
                handle["frame_id"] = frame_id
            try:
                context_id = _arm_collector(client, frame_id, timeout=_budget(remaining))
                handle["context_id"] = context_id
                snapshot = _read_snapshot(client, context_id, timeout=_budget(remaining))
                break
            except (js.JSException, CDPError, CDPTransportError) as error:
                last_error = error
        if snapshot is None:
            raise (
                last_error
                if last_error is not None
                else ValueError("vitals: the collector could not be armed")
            )
    return _vitals_result(
        snapshot,
        requested_url=requested_url,
        browser_version=browser_version,
        interaction_requested=interaction_requested,
        allowed_origins=allowed_origins,
        navigation_source=navigation_source,
        navigation_step=navigation_step,
        pre_interaction_url=pre_interaction_url,
        measurement_environment=measurement_environment,
    )


def browser_version(client: CDPClient, timeout: float | None = None) -> str | None:
    """Best-effort Chrome product string used to bind a proof to its engine."""
    try:
        result = client.send("Browser.getVersion", timeout=timeout)
    except CDPError, CDPTransportError:
        return None
    product = result.get("product")
    return product if isinstance(product, str) and product else None


def vitals(
    client: CDPClient,
    url: str,
    timeout: float = 30.0,
    click_selector: str | None = None,
    settle: float = 0.5,
    origins: str | None = None,
) -> dict[str, Any]:
    """Navigate, interact and bind a vitals proof under the run's origin policy.

    The real origin is judged immediately after the navigation and again
    immediately after the optional interaction — always before the isolated
    world is created, the collector is armed or any snapshot is read — and
    the snapshot's own document binding is judged one last time before the
    result is returned. The collector is armed BEFORE the interaction so the
    Event Timing observer is live when the click happens.
    """
    timeout = validate_time_budget(timeout, "vitals timeout")
    settle = validate_time_budget(settle, "vitals settle")
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise CDPTimeout(f"vitals timeout after {timeout}s")
        return budget

    allowed = parse_origins(origins, required=True) if origins else None

    def judge_current(phase: str) -> str | None:
        current = actions.current_http_url(client, timeout=remaining())
        if allowed is not None:
            if current is None:
                raise ValueError(f"unable to determine the current URL {phase}")
            assert_url_allowed(current, allowed)
        return current

    handle: dict[str, Any] = {}
    try:
        nav.navigate(client, url, timeout=remaining())
        pre_interaction_url = judge_current("after vitals navigation")
        handle = install_vitals_observer(client, timeout=remaining())
        if click_selector:
            inputs.click(client, click_selector, remaining=remaining)
            judge_current("after vitals interaction")
        version = browser_version(client, timeout=remaining())
        return {
            "url": url,
            **collect_vitals(
                client,
                settle=min(settle, remaining()),
                handle=handle,
                requested_url=url,
                browser_version=version,
                interaction_requested=click_selector is not None,
                allowed_origins=allowed,
                pre_interaction_url=pre_interaction_url,
                origin_guard=lambda: judge_current("before vitals capture"),
                remaining=remaining,
            ),
        }
    finally:
        release_vitals_collector(client, handle)


def _budget(remaining: Callable[[], float] | None) -> float | None:
    return remaining() if remaining is not None else None


def _main_frame_id(client: CDPClient, timeout: float | None = None) -> str:
    result = client.send("Page.getFrameTree", timeout=timeout)
    frame_id = result.get("frameTree", {}).get("frame", {}).get("id")
    if not isinstance(frame_id, str) or not frame_id:
        raise ValueError("vitals: unable to determine the main frame")
    return frame_id


def _arm_collector(
    client: CDPClient,
    frame_id: str,
    timeout: float | None = None,
) -> int:
    world = client.send(
        "Page.createIsolatedWorld",
        {"frameId": frame_id, "worldName": VITALS_WORLD_NAME},
        timeout=timeout,
    )
    context_id = world.get("executionContextId")
    if isinstance(context_id, bool) or not isinstance(context_id, int) or context_id <= 0:
        raise ValueError("vitals: Chrome did not return an isolated world context")
    _evaluate_in_world(client, context_id, VITALS_COLLECTOR_SOURCE, timeout=timeout)
    return context_id


def _read_snapshot(
    client: CDPClient,
    context_id: int,
    timeout: float | None = None,
) -> dict[str, Any]:
    value = _evaluate_in_world(client, context_id, "__cdpxVitalsRead()", timeout=timeout)
    if not isinstance(value, dict):
        raise js.JSException("vitals collector snapshot is not an object")
    return value


def _evaluate_in_world(
    client: CDPClient,
    context_id: int,
    expression: str,
    timeout: float | None = None,
) -> Any:
    response = client.send(
        "Runtime.evaluate",
        {"expression": expression, "contextId": context_id, "returnByValue": True},
        timeout=timeout,
    )
    if "exceptionDetails" in response:
        details = response["exceptionDetails"]
        text = details.get("exception", {}).get("description") or details.get("text", "JS error")
        raise js.JSException(text)
    return response.get("result", {}).get("value")


def _vitals_result(
    snapshot: dict[str, Any],
    *,
    requested_url: str | None,
    browser_version: str | None,
    interaction_requested: bool,
    allowed_origins: tuple[str, ...] | None,
    navigation_source: str | None,
    navigation_step: str | None,
    pre_interaction_url: str | None,
    measurement_environment: dict[str, Any] | None,
) -> dict[str, Any]:
    errors: list[str] = []
    raw_errors = snapshot.get("errors")
    if not isinstance(raw_errors, list):
        errors.append("collector errors block: list required")
        raw_errors = []
    errors.extend(str(item)[:200] for item in raw_errors[:MAX_SNAPSHOT_ERRORS])
    document_observed = snapshot.get("document_observed") is True
    if not document_observed:
        errors.append("collector did not observe the current document")
    if snapshot.get("schema") != VITALS_SCHEMA:
        errors.append("collector snapshot schema mismatch")
    if snapshot.get("collector_version") != VITALS_COLLECTOR_VERSION:
        errors.append("collector snapshot version mismatch")
    dropped_entries = _bounded_count(snapshot.get("dropped_entries"), "dropped_entries", errors)
    interaction_count = _bounded_count(
        snapshot.get("interaction_entry_count"), "interaction_entry_count", errors
    )
    arm_scope = snapshot.get("arm_scope")
    if arm_scope not in VITALS_ARM_SCOPES:
        errors.append("collector arm_scope: document-start or capture-time required")
        arm_scope = "capture-time"
    supported = _normalize_supported(snapshot.get("supported"))
    context = snapshot.get("context")
    context = context if isinstance(context, dict) else {}
    document_url = context.get("document_url")
    if not isinstance(document_url, str) or not document_url:
        errors.append("document binding missing from collector snapshot")
        document_url = None
    time_origin = _bounded_time_origin(context.get("time_origin"), errors)
    metrics, metric_errors = _normalize_metrics(snapshot.get("metrics"), supported)
    errors.extend(metric_errors)
    measured = not errors
    if measured and allowed_origins and document_url is not None:
        # The binding is judged like any other navigation: a snapshot read
        # from a document outside the run's policy is never returned.
        assert_url_allowed(document_url, allowed_origins)
    status = "unavailable"
    partial_reasons: list[str] = []
    if measured:
        if dropped_entries > 0:
            status = "partial"
            partial_reasons.append(
                f"the browser announced {dropped_entries} dropped performance entries: "
                "the buffered entry history is incomplete"
            )
        else:
            status = "measured"
    result: dict[str, Any] = {
        "schema": VITALS_SCHEMA,
        "collector_version": VITALS_COLLECTOR_VERSION,
        "status": status,
        "collector": {
            "document_observed": document_observed,
            "scope": "isolated-world/main-frame",
            "arm_scope": arm_scope,
            "supported": supported,
            "dropped_entries": dropped_entries,
            "errors": errors[:MAX_SNAPSHOT_ERRORS],
        },
        "interaction": {
            "requested": interaction_requested,
            "observed": measured and interaction_count > 0,
            "entry_count": interaction_count if measured else 0,
        },
        "metrics": metrics if measured else None,
        "document": {
            "requested_url": requested_url,
            "document_url": document_url,
            "time_origin": time_origin,
            "navigation_source": _resolve_navigation_source(
                explicit=navigation_source,
                requested_url=requested_url,
                pre_interaction_url=pre_interaction_url,
                document_url=document_url,
            ),
            "navigation_step": (
                navigation_step[:120]
                if isinstance(navigation_step, str) and navigation_step
                else None
            ),
            "navigation_type": _bounded_context_string(context.get("navigation_type")),
            "frame_scope": "main-frame",
            "viewport": _normalize_viewport(context.get("viewport")),
        },
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not measured:
        result["unavailable_reason"] = errors[0] if errors else "collector unavailable"
    if partial_reasons:
        result["partial_reasons"] = partial_reasons
    if measurement_environment is not None:
        result["measurement_environment"] = measurement_environment
    if browser_version is not None:
        result["browser_version"] = browser_version
    return result


def _resolve_navigation_source(
    *,
    explicit: str | None,
    requested_url: str | None,
    pre_interaction_url: str | None,
    document_url: str | None,
) -> str | None:
    if explicit is not None:
        if explicit not in VITALS_NAVIGATION_SOURCES:
            raise ValueError(f"vitals: unknown navigation source: {explicit}")
        return explicit
    if document_url is None:
        return None
    if pre_interaction_url is not None and document_url != pre_interaction_url:
        return "click"
    if requested_url is not None:
        return "redirect" if document_url != requested_url else "goto"
    return "current-document"


def _normalize_metrics(
    value: Any,
    supported: dict[str, bool],
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    data = value if isinstance(value, dict) else None
    if data is None:
        return None, ["metrics block missing from collector snapshot"]
    lcp = _bounded_number(data.get("lcp"), MAX_TIME_MS, "lcp", errors)
    cls = _bounded_number(data.get("cls"), MAX_WINDOW_VALUE, "cls", errors)
    raw_sum = _bounded_number(data.get("raw_sum"), MAX_RAW_SUM, "raw_sum", errors)
    inp = _bounded_number(data.get("inp"), MAX_DURATION_MS, "inp", errors)
    total_entries = _bounded_count(data.get("total_entries"), "total_entries", errors)
    ignored = _bounded_count(data.get("ignored_recent_input"), "ignored_recent_input", errors)
    winning = _normalize_cls_window(data.get("winning_window"), total_entries, errors)
    if cls is not None and raw_sum is not None and cls > raw_sum + 1e-9:
        errors.append("cls exceeds raw_sum: incoherent counters")
    if cls is not None and cls > 0 and winning is None:
        errors.append("cls above zero without a winning window: incoherent counters")
    if cls is not None and winning is not None and abs(winning["value"] - cls) > 1e-9:
        errors.append("winning_window.value differs from cls: incoherent counters")
    if total_entries == 0 and raw_sum is not None and raw_sum > 0:
        errors.append("raw_sum above zero without eligible entries: incoherent counters")
    if errors:
        return None, errors
    return {
        "lcp": _metric_availability(supported["lcp"], lcp),
        "cls": (
            {
                "status": "measured",
                "value": cls,
                "raw_sum": raw_sum,
                "total_entries": total_entries,
                "ignored_recent_input": ignored,
                "winning_window": winning,
            }
            if supported["layout_shift"]
            else {"status": "unsupported", "value": None}
        ),
        "inp": _metric_availability(supported["event_timing"], inp),
    }, []


def _metric_availability(supported: bool, value: float | None) -> dict[str, Any]:
    """Per-metric availability: an unsupported entry type is never a zero."""
    return {
        "status": "measured" if supported else "unsupported",
        "value": value if supported else None,
    }


def _normalize_cls_window(
    value: Any,
    total_entries: int,
    errors: list[str],
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append("winning_window: object or null required")
        return None
    raw_entries = value.get("entries")
    entries = raw_entries if isinstance(raw_entries, list) else []
    if not isinstance(raw_entries, list):
        errors.append("winning_window.entries: list required")
    window_value = _bounded_number(
        value.get("value"), MAX_WINDOW_VALUE, "winning_window.value", errors
    )
    start_time = _bounded_number(
        value.get("start_time"), MAX_TIME_MS, "winning_window.start_time", errors
    )
    end_time = _bounded_number(
        value.get("end_time"), MAX_TIME_MS, "winning_window.end_time", errors
    )
    duration = _bounded_number(
        value.get("duration"), MAX_TIME_MS, "winning_window.duration", errors
    )
    entry_count = _bounded_count(value.get("entry_count"), "winning_window.entry_count", errors)
    normalized = [_normalize_cls_entry(entry, errors) for entry in entries[:MAX_CLS_ENTRIES]]
    entries_truncated = bool(value.get("entries_truncated")) or len(entries) > MAX_CLS_ENTRIES
    if entry_count < len(normalized):
        errors.append("winning_window.entry_count below retained entries: incoherent counters")
    if entry_count > total_entries:
        errors.append("winning_window.entry_count above total_entries: incoherent counters")
    if entry_count > MAX_CLS_ENTRIES and not entries_truncated:
        errors.append("winning_window.entries_truncated missing for oversized window")
    if end_time < start_time:
        errors.append("winning_window end_time before start_time: incoherent counters")
    if abs(duration - max(0.0, end_time - start_time)) > 1e-9:
        errors.append(
            "winning_window.duration differs from end_time - start_time: incoherent counters"
        )
    if any(entry["had_recent_input"] for entry in normalized):
        errors.append("winning_window retains an entry with had_recent_input: incoherent counters")
    if not entries_truncated and entry_count <= MAX_CLS_ENTRIES:
        retained_sum = math.fsum(entry["value"] for entry in normalized)
        if not math.isclose(retained_sum, window_value, rel_tol=1e-9, abs_tol=1e-12):
            errors.append(
                "winning_window.value differs from the sum of its retained entries: "
                "incoherent counters"
            )
    return {
        "value": window_value,
        "start_time": start_time,
        "end_time": end_time,
        "duration": duration,
        "entry_count": entry_count,
        "entries": normalized,
        "entries_truncated": entries_truncated,
    }


def _normalize_cls_entry(value: Any, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("winning_window.entries: objects required")
        value = {}
    raw_sources = value.get("sources")
    sources = raw_sources if isinstance(raw_sources, list) else []
    if not isinstance(raw_sources, list):
        errors.append("winning_window.entries.sources: list required")
    had_recent_input = value.get("had_recent_input")
    if not isinstance(had_recent_input, bool):
        errors.append("winning_window.entries.had_recent_input: boolean required")
    normalized_sources = [
        _normalize_cls_source(source, errors) for source in sources[:MAX_CLS_SOURCES_PER_ENTRY]
    ]
    source_count = _bounded_count(value.get("source_count"), "entries.source_count", errors)
    sources_truncated = (
        bool(value.get("sources_truncated")) or len(sources) > MAX_CLS_SOURCES_PER_ENTRY
    )
    if source_count < len(normalized_sources):
        errors.append("entries.source_count below retained sources: incoherent counters")
    if source_count > MAX_CLS_SOURCES_PER_ENTRY and not sources_truncated:
        errors.append("entries.sources_truncated missing for oversized source list")
    return {
        "value": _bounded_number(value.get("value"), MAX_ENTRY_VALUE, "entries.value", errors),
        "start_time": _bounded_number(
            value.get("start_time"), MAX_TIME_MS, "entries.start_time", errors
        ),
        "duration": _bounded_number(
            value.get("duration"), MAX_DURATION_MS, "entries.duration", errors
        ),
        "had_recent_input": had_recent_input is True,
        "sources": normalized_sources,
        "source_count": source_count,
        "sources_truncated": sources_truncated,
    }


def _normalize_cls_source(value: Any, errors: list[str]) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    if not isinstance(value, dict):
        errors.append("entries.sources: objects required")
    return {
        "node": _normalize_cls_node(source.get("node"), errors),
        "previous_rect": _normalize_rect(source.get("previous_rect"), errors),
        "current_rect": _normalize_rect(source.get("current_rect"), errors),
    }


def _normalize_cls_node(value: Any, errors: list[str]) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append("entries.sources.node: object or null required")
        return None
    classes = value.get("classes")
    if not isinstance(classes, list):
        errors.append("entries.sources.node.classes: list required")
        classes = []
    return {
        "tag": _bounded_string(value.get("tag"), 32, "entries.sources.node.tag", errors),
        "id": _bounded_string(value.get("id"), 120, "entries.sources.node.id", errors),
        "classes": [
            _bounded_string(item, 80, "entries.sources.node.classes", errors)
            for item in classes[:MAX_CLS_CLASSES_PER_NODE]
        ],
        "selector": _bounded_string(
            value.get("selector"), 240, "entries.sources.node.selector", errors
        ),
    }


def _normalize_rect(value: Any, errors: list[str]) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append("entries.sources.rect: object or null required")
        return None
    rect: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        rect[key] = _bounded_number(value.get(key), MAX_COORDINATE, f"rect.{key}", errors)
    return rect


def _normalize_supported(value: Any) -> dict[str, bool]:
    data = value if isinstance(value, dict) else {}
    return {key: data.get(key) is True for key in ("lcp", "layout_shift", "event_timing")}


def _bounded_context_string(value: Any) -> str | None:
    return value[:32] if isinstance(value, str) and value else None


def _normalize_viewport(value: Any) -> dict[str, float | int | None]:
    data = value if isinstance(value, dict) else {}
    viewport: dict[str, float | int | None] = {}
    for key in ("width", "height"):
        number = data.get(key)
        if (
            isinstance(number, int | float)
            and not isinstance(number, bool)
            and math.isfinite(float(number))
            and 0 <= float(number) <= MAX_COORDINATE
        ):
            viewport[key] = int(number)
        else:
            viewport[key] = None
    dpr = data.get("dpr")
    if (
        isinstance(dpr, int | float)
        and not isinstance(dpr, bool)
        and math.isfinite(float(dpr))
        and 0 < float(dpr) <= 100
    ):
        viewport["dpr"] = float(dpr)
    else:
        viewport["dpr"] = None
    return viewport


def _bounded_time_origin(value: Any, errors: list[str]) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        errors.append("document time_origin: finite number required")
        return None
    number = float(value)
    if number < 0 or number > MAX_TIME_ORIGIN_MS:
        errors.append(f"document time_origin: outside physical bounds [0, {MAX_TIME_ORIGIN_MS:g}]")
        return None
    return number


def _bounded_number(value: Any, limit: float, label: str, errors: list[str]) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        errors.append(f"{label}: finite number required")
        return 0.0
    number = float(value)
    if number < 0 or number > limit:
        errors.append(f"{label}: outside physical bounds [0, {limit:g}]")
    return number


def _bounded_count(value: Any, label: str, errors: list[str]) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > MAX_ENTRY_COUNT
    ):
        errors.append(f"{label}: non-negative integer required")
        return 0
    return value


def _bounded_string(value: Any, limit: int, label: str, errors: list[str]) -> str:
    if isinstance(value, str):
        return value[:limit]
    errors.append(f"{label}: string required")
    return ""


def a11y(client: CDPClient) -> dict[str, Any]:
    response = client.send("Accessibility.getFullAXTree")
    nodes = [
        {
            "role": _ax_value(node.get("role")),
            "name": _ax_value(node.get("name")),
            "ignored": node.get("ignored", False),
        }
        for node in response.get("nodes", [])
        if not node.get("ignored", False)
    ]
    return {"nodes": nodes, "count": len(nodes)}


def coverage(client: CDPClient, url: str, timeout: float = 30.0) -> dict[str, Any]:
    client.send("DOM.enable")
    client.send("CSS.enable")
    client.send("Profiler.enable")
    client.send("CSS.startRuleUsageTracking")
    client.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
    nav.navigate(client, url, timeout=timeout)
    js_response = client.send("Profiler.takePreciseCoverage")
    css_response = client.send("CSS.stopRuleUsageTracking")
    client.send("Profiler.stopPreciseCoverage")
    files = []
    for item in js_response.get("result", []):
        byte_counts = _coverage_bytes(item.get("functions", []))
        total = byte_counts["total_bytes"]
        files.append(
            {
                "url": item.get("url"),
                "functions": len(item.get("functions", [])),
                "used_ranges": sum(
                    1
                    for function in item.get("functions", [])
                    for byte_range in function.get("ranges", [])
                    if (byte_range.get("count") or 0) > 0
                ),
                **byte_counts,
                "coverage_percent": (
                    round(byte_counts["used_bytes"] * 100 / total, 1) if total else None
                ),
            }
        )
    css_rules = css_response.get("ruleUsage", [])
    js_totals = {
        key: sum(item[key] for item in files)
        for key in ("total_bytes", "used_bytes", "unused_bytes")
    }
    return {
        "url": url,
        "files": files,
        "count": len(files),
        "js": js_totals,
        "css": {
            "rules": len(css_rules),
            "used": sum(1 for rule in css_rules if rule.get("used")),
            "unused": sum(1 for rule in css_rules if not rule.get("used")),
        },
    }


def _coverage_bytes(functions: list[dict[str, Any]]) -> dict[str, int]:
    ranges = [byte_range for function in functions for byte_range in function.get("ranges", [])]
    boundaries = sorted(
        {
            offset
            for byte_range in ranges
            for offset in (
                byte_range.get("startOffset", 0),
                byte_range.get("endOffset", 0),
            )
        }
    )
    used = unused = 0
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        covering = [
            byte_range
            for byte_range in ranges
            if byte_range.get("startOffset", 0) <= start and byte_range.get("endOffset", 0) >= end
        ]
        if not covering:
            continue
        most_specific = min(
            covering,
            key=lambda byte_range: (
                byte_range.get("endOffset", 0) - byte_range.get("startOffset", 0)
            ),
        )
        if (most_specific.get("count") or 0) > 0:
            used += end - start
        else:
            unused += end - start
    return {"total_bytes": used + unused, "used_bytes": used, "unused_bytes": unused}


def _ax_value(value: dict[str, Any] | None) -> str | None:
    if isinstance(value, dict):
        result = value.get("value")
        return result if isinstance(result, str) else None
    return None
