"""Adaptateur Symfony Web Profiler: fetch page-context + parsing HTML des panels.

Le WebProfilerBundle n'expose aucune API JSON: la seule source structurée est
le HTML des pages `/_profiler/{token}?panel=X`. Ce module en extrait un contrat
JSON stable par panel (db, twig, cache, exception, http_client, messenger,
router, time, logger).

Principes:
- fetch DANS la page (fetch() via Runtime.evaluate): même origine que l'app,
  cookies et résolution DNS du navigateur — indispensable quand le host n'est
  visible que du navigateur (Docker, port-forward);
- parsing stdlib html.parser fondé sur les marqueurs les plus stables du
  WebProfilerBundle (blocs `class="metric"` label/valeur, tables, sidebar),
  jamais de chemin CSS profond;
- tolérance totale: panel absent -> {"available": false}, HTML imparsable ->
  {"available": true, "parse_error": ...} partiel. Jamais d'exception de parse.

Les durées (`*_ms`) sont indicatives: les tests n'assertent que des comptes,
classes, routes et statuts.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any

from cdpx.client import CDPClient
from cdpx.primitives import js

# Clé de sortie -> valeur du paramètre ?panel= du WebProfilerBundle.
PANEL_SOURCES: dict[str, str] = {
    "router": "request",
    "time": "time",
    "db": "db",
    "twig": "twig",
    "cache": "cache",
    "exception": "exception",
    "http_client": "http_client",
    "messenger": "messenger",
    "logger": "logger",
}
ALL_PANELS: tuple[str, ...] = tuple(PANEL_SOURCES)

# Bornage des listes best-effort (requêtes SQL, templates, traces HTTP...).
LIST_LIMIT = 20

# Le marqueur __cdpx_profiler_panels sert au scripting du mock CDP (on_eval).
PANEL_FETCH_JS = """
(async () => { const __cdpx_profiler_panels = 1;
  const targets = %s;
  const one = async ([panel, url]) => {
    try {
      const res = await fetch(url, {
        headers: {Accept: 'text/html'},
        credentials: 'same-origin',
        signal: AbortSignal.timeout(%d),
      });
      const html = await res.text();
      return {panel, status: res.status, html};
    } catch (e) {
      return {panel, status: 0, html: '', error: String(e)};
    }
  };
  return JSON.stringify(await Promise.all(targets.map(one)));
})()
"""


def normalize_panels(panels: list[str] | tuple[str, ...] | None) -> list[str]:
    """Valide une liste de panels demandés (None -> tous)."""
    if panels is None:
        return list(ALL_PANELS)
    unknown = [p for p in panels if p not in PANEL_SOURCES]
    if unknown:
        raise ValueError(
            f"panel(s) inconnu(s): {', '.join(unknown)} (choix: {', '.join(ALL_PANELS)})"
        )
    return list(panels)


def fetch_panels(
    client: CDPClient, profiler_url: str, panels: list[str], timeout: float = 30.0
) -> list[dict[str, Any]]:
    """Récupère le HTML des panels demandés via fetch() dans la page."""
    base = profiler_url.split("?", 1)[0].split("#", 1)[0]
    targets = [[key, f"{base}?panel={PANEL_SOURCES[key]}"] for key in panels]
    expr = PANEL_FETCH_JS % (json.dumps(targets), int(timeout * 1000))
    raw = js.evaluate(client, expr, await_promise=True)
    if not isinstance(raw, str):
        return []
    fetched = json.loads(raw)
    return fetched if isinstance(fetched, list) else []


def collect(
    client: CDPClient,
    hit: dict[str, Any],
    panels: list[str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Contrat complet de `cdpx profiler` à partir d'un hit X-Debug-Token(-Link).

    `hit` vient de dev.find_profiler_hit: {url, status, link, headers}.
    """
    keys = normalize_panels(panels) if panels is None else list(panels)
    link = str(hit["link"])
    token = link.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
    out: dict[str, Any] = {
        "token": token,
        "url": hit["url"],
        "status": hit["status"],
        "profiler_url": link,
        "profiler_status": None,
        "response_headers": hit["headers"],
        "panels": {},
    }
    if not keys:
        return out
    fetched = {item.get("panel"): item for item in fetch_panels(client, link, keys, timeout)}
    first = fetched.get(keys[0])
    if first is not None:
        out["profiler_status"] = first.get("status")
    for key in keys:
        item = fetched.get(key) or {"status": 0, "html": ""}
        out["panels"][key] = parse_panel(key, int(item.get("status") or 0), item.get("html") or "")
    return out


def parse_panel(key: str, status: int, html: str) -> dict[str, Any]:
    """Parse un panel; ne lève jamais (parse_error en cas d'imprévu)."""
    if key not in PANEL_SOURCES:
        raise ValueError(f"panel inconnu: {key}")
    if status != 200 or not html:
        return {"available": False, "status": status}
    parser = _PARSERS[key]
    try:
        parsed = parser(html)
    except Exception as e:  # noqa: BLE001 - contrat: jamais d'exception de parse
        return {"available": True, "parse_error": f"{type(e).__name__}: {e}"}
    return {"available": True, **parsed}


# -- extracteurs génériques -----------------------------------------------------

_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
    return set((dict(attrs).get("class") or "").split())


class _MetricsParser(HTMLParser):
    """Blocs `class="metric"` du WebProfilerBundle: {label, value, unit, heading}.

    C'est le markup le plus stable des panels (div.metric > span.value + span.label,
    unité éventuelle en span.unit imbriqué dans la valeur).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metrics: list[dict[str, str]] = []
        self.heading = ""
        self._depth = 0
        self._metric_at: int | None = None
        self._value_at: int | None = None
        self._unit_at: int | None = None
        self._label_at: int | None = None
        self._heading_at: int | None = None
        self._value: list[str] = []
        self._unit: list[str] = []
        self._label: list[str] = []
        self._head: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            return
        self._depth += 1
        classes = _classes(attrs)
        if tag in ("h2", "h3", "h4") and self._heading_at is None:
            self._heading_at = self._depth
            self._head = []
        if "metric" in classes and self._metric_at is None:
            self._metric_at = self._depth
            self._value, self._unit, self._label = [], [], []
        if self._metric_at is None:
            return
        if "value" in classes and self._value_at is None:
            self._value_at = self._depth
        elif "unit" in classes and self._unit_at is None and self._value_at is not None:
            self._unit_at = self._depth
        elif "label" in classes and self._label_at is None:
            self._label_at = self._depth

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if self._unit_at == self._depth:
            self._unit_at = None
        if self._value_at == self._depth:
            self._value_at = None
        if self._label_at == self._depth:
            self._label_at = None
        if self._heading_at == self._depth:
            self.heading = _norm("".join(self._head))
            self._heading_at = None
        if self._metric_at == self._depth:
            self.metrics.append(
                {
                    "label": _norm("".join(self._label)),
                    "value": _norm("".join(self._value)),
                    "unit": _norm("".join(self._unit)),
                    "heading": self.heading,
                }
            )
            self._metric_at = None
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._heading_at is not None:
            self._head.append(data)
        if self._unit_at is not None:
            self._unit.append(data)
            return
        if self._value_at is not None:
            self._value.append(data)
        elif self._label_at is not None:
            self._label.append(data)


class _TablesParser(HTMLParser):
    """Tables associées au dernier heading h2/h3/h4 rencontré."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict[str, Any]] = []
        self._depth = 0
        self._heading_at: int | None = None
        self._head: list[str] = []
        self.heading = ""
        self._table_at: int | None = None
        self._cell_at: int | None = None
        self._cell_kind = ""
        self._cell: list[str] = []
        self._row: list[str] = []
        self._row_is_header = False
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            return
        self._depth += 1
        if tag in ("h2", "h3", "h4") and self._heading_at is None:
            self._heading_at = self._depth
            self._head = []
        if tag == "table" and self._table_at is None:
            self._table_at = self._depth
            self._current = {"heading": self.heading, "headers": [], "rows": []}
        if self._table_at is None:
            return
        if tag == "tr":
            self._row = []
            self._row_is_header = False
        elif tag in ("td", "th") and self._cell_at is None:
            self._cell_at = self._depth
            self._cell_kind = tag
            self._cell = []
            if tag == "th":
                self._row_is_header = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if self._heading_at == self._depth:
            self.heading = _norm("".join(self._head))
            self._heading_at = None
        if self._cell_at == self._depth and tag in ("td", "th"):
            self._row.append(_norm("".join(self._cell)))
            self._cell_at = None
        if tag == "tr" and self._current is not None and self._row:
            if self._row_is_header and not self._current["headers"]:
                self._current["headers"] = self._row
            elif not self._row_is_header:
                self._current["rows"].append(self._row)
            self._row = []
        if tag == "table" and self._table_at == self._depth:
            if self._current is not None:
                self.tables.append(self._current)
            self._current = None
            self._table_at = None
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._heading_at is not None:
            self._head.append(data)
        if self._cell_at is not None:
            self._cell.append(data)


def _metrics(html: str) -> list[dict[str, str]]:
    parser = _MetricsParser()
    parser.feed(html)
    return parser.metrics


def _tables(html: str) -> list[dict[str, Any]]:
    parser = _TablesParser()
    parser.feed(html)
    return parser.tables


def _menu(html: str) -> set[str]:
    """Panels annoncés par la sidebar (liens ?panel=X). Recoupement best-effort."""
    return set(re.findall(r'href="[^"]*[?&](?:amp;)?panel=([a-zA-Z_]+)"', html))


def _metric(
    metrics: list[dict[str, str]], *needles: str, heading: str | None = None
) -> dict[str, str] | None:
    for metric in metrics:
        label = metric["label"].lower()
        if all(needle in label for needle in needles):
            if heading is not None and heading not in metric["heading"].lower():
                continue
            return metric
    return None


def _int(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"-?\d[\d\s,.\u00a0\u202f]*", text)
    if not match:
        return None
    digits = re.sub(r"[^\d-]", "", match.group(0).rstrip(",."))
    return int(digits) if digits not in ("", "-") else None


def _float(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text.replace(" ", "").replace(",", ""))
    return float(match.group(0)) if match else None


def _ms(metric: dict[str, str] | None) -> float | None:
    """Valeur d'un metric temporel convertie en millisecondes."""
    if metric is None:
        return None
    value = _float(metric["value"])
    if value is None:
        return None
    unit = metric["unit"].lower()
    if unit == "s":
        return value * 1000
    if unit in ("µs", "us"):
        return value / 1000
    return value


def _metric_int(metrics: list[dict[str, str]], *needles: str, heading: str | None = None) -> int:
    metric = _metric(metrics, *needles, heading=heading)
    value = _int(metric["value"]) if metric else None
    return value if value is not None else 0


def _find_table(tables: list[dict[str, Any]], *needles: str) -> dict[str, Any] | None:
    """Première table dont un header ou le heading contient tous les fragments."""
    for table in tables:
        haystack = " ".join(table["headers"] + [table["heading"]]).lower()
        if all(needle in haystack for needle in needles):
            return table
    return None


def _column(table: dict[str, Any], *needles: str) -> int | None:
    for idx, header in enumerate(table["headers"]):
        low = header.lower()
        if all(needle in low for needle in needles):
            return idx
    return None


_FQCN_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:\\[A-Z][A-Za-z0-9_]*)+\b")


# -- parseurs par panel -----------------------------------------------------------


def _parse_db(html: str) -> dict[str, Any]:
    metrics = _metrics(html)
    queries = _metric_int(metrics, "database queries")
    statements = _metric_int(metrics, "different statements")
    out: dict[str, Any] = {
        "queries": queries,
        "statements": statements,
        "duplicates": max(0, queries - statements),
        "time_ms": _ms(_metric(metrics, "query time")),
        "list": [],
    }
    table = _find_table(_tables(html), "info")
    if table:
        sql_col = _column(table, "info")
        time_col = _column(table, "time")
        for row in table["rows"][:LIST_LIMIT]:
            if sql_col is None or sql_col >= len(row):
                continue
            entry: dict[str, Any] = {"sql": row[sql_col]}
            if time_col is not None and time_col < len(row):
                entry["duration_ms"] = _float(row[time_col])
            out["list"].append(entry)
    return out


def _parse_twig(html: str) -> dict[str, Any]:
    metrics = _metrics(html)
    out: dict[str, Any] = {
        "templates": _metric_int(metrics, "template calls"),
        "blocks": _metric_int(metrics, "block calls"),
        "macros": _metric_int(metrics, "macro calls"),
        "render_ms": _ms(_metric(metrics, "render time")),
        "list": [],
    }
    table = _find_table(_tables(html), "template")
    if table:
        for row in table["rows"][:LIST_LIMIT]:
            if row and row[0]:
                out["list"].append(row[0])
    return out


def _parse_cache(html: str) -> dict[str, Any]:
    metrics = _metrics(html)
    totals = [m for m in metrics if m["label"].lower().startswith("total")]
    scope = totals or metrics
    out: dict[str, Any] = {
        "calls": _metric_int(scope, "calls"),
        "reads": _metric_int(scope, "reads"),
        "hits": _metric_int(scope, "hits"),
        "misses": _metric_int(scope, "misses"),
        "writes": _metric_int(scope, "writes"),
        "deletes": _metric_int(scope, "deletes"),
        "time_ms": _ms(_metric(scope, "time")),
        "pools": {},
    }
    for metric in metrics:
        heading = metric["heading"]
        # Les groupes par pool sont titrés par le nom du service (contient un point),
        # ex. "app.scenario_pool" ou "cache.app".
        if "." not in heading or metric in totals:
            continue
        pool = out["pools"].setdefault(
            heading,
            {"calls": 0, "reads": 0, "hits": 0, "misses": 0, "writes": 0, "deletes": 0},
        )
        label = metric["label"].lower()
        for key in pool:
            if key in label:
                value = _int(metric["value"])
                if value is not None:
                    pool[key] = value
    return out


def _parse_exception(html: str) -> dict[str, Any]:
    lowered = html.lower()
    if "no exception was thrown" in lowered:
        return {"raised": False, "class": None, "message": None}
    message = None
    match = re.search(r'class="[^"]*exception-message[^"]*"[^>]*>(.*?)</', html, flags=re.DOTALL)
    if match:
        message = _norm(re.sub(r"<[^>]+>", " ", match.group(1)))
    exception_class = None
    abbr = re.search(r'<abbr[^>]*title="([^"]+)"', html)
    if abbr and _FQCN_RE.search(abbr.group(1)):
        exception_class = abbr.group(1)
    else:
        for candidate in _FQCN_RE.findall(html):
            if candidate.endswith(("Exception", "Error")):
                exception_class = candidate
                break
    return {"raised": True, "class": exception_class, "message": message}


def _parse_http_client(html: str) -> dict[str, Any]:
    metrics = _metrics(html)
    requests_count = _metric_int(metrics, "total requests")
    errors = _metric_int(metrics, "error")
    out: dict[str, Any] = {
        "clients": 0,
        "requests": requests_count,
        "errors": errors,
        "list": [],
    }
    # Les sections par client sont titrées en h3 (le titre du panel est en h2).
    sections = {
        _norm(re.sub(r"<[^>]+>", " ", m.group(1)))
        for m in re.finditer(r"<h3[^>]*>(.*?)</h3>", html, flags=re.DOTALL)
    }
    sections.discard("")
    for match in re.finditer(
        r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b\s+(https?://[^\s\"'<]+)", html
    ):
        if len(out["list"]) >= LIST_LIMIT:
            break
        out["list"].append({"method": match.group(1), "url": match.group(2)})
    statuses = [
        _int(m.group(1))
        for m in re.finditer(r'class="[^"]*status-code[^"]*"[^>]*>\s*(\d{3})', html)
    ]
    for idx, status in enumerate(statuses[: len(out["list"])]):
        out["list"][idx]["status"] = status
    if not errors:
        out["errors"] = sum(1 for s in statuses if s is not None and s >= 400)
    out["clients"] = len(sections) or (1 if requests_count else 0)
    return out


def _parse_messenger(html: str) -> dict[str, Any]:
    metrics = _metrics(html)
    tables = _tables(html)
    buses: dict[str, int] = {}
    for metric in metrics:
        if "bus" in metric["heading"].lower() and "message" in metric["label"].lower():
            value = _int(metric["value"])
            if value is not None:
                buses[metric["heading"]] = value
    classes: list[str] = []
    for table in tables:
        for row in table["rows"]:
            for cell in row:
                fqcn = _FQCN_RE.search(cell)
                if fqcn and "\\Message" in fqcn.group(0):
                    classes.append(fqcn.group(0))
                    break
    dispatched = sum(buses.values()) if buses else len(classes)
    lowered = html.lower()
    no_handler = lowered.count("no handler")
    out: dict[str, Any] = {
        "dispatched": dispatched,
        "handled": max(0, dispatched - no_handler),
        "buses": buses,
        "list": [{"class": c} for c in classes[:LIST_LIMIT]],
    }
    return out


def _parse_router(html: str) -> dict[str, Any]:
    tables = _tables(html)
    route = None
    controller = None
    for table in tables:
        for row in table["rows"]:
            if len(row) < 2:
                continue
            key = row[0].strip()
            if key == "_route" and route is None:
                route = row[1]
            elif key == "_controller" and controller is None:
                controller = _norm(row[1])
    status_code = None
    for table in tables:
        for row in table["rows"]:
            if len(row) >= 2 and "status" in row[0].lower():
                status_code = _int(row[1])
                break
        if status_code is not None:
            break
    if status_code is None:
        metric = _metric(_metrics(html), "status")
        status_code = _int(metric["value"]) if metric else None
    return {
        "route": route,
        "controller": controller,
        "status_code": status_code,
        "redirect": bool(status_code and 300 <= status_code < 400),
    }


def _parse_time(html: str) -> dict[str, Any]:
    metrics = _metrics(html)
    out: dict[str, Any] = {
        "total_ms": _ms(_metric(metrics, "total execution time")),
        "init_ms": _ms(_metric(metrics, "initialization")),
        "events": _timeline_events(html),
    }
    return out


def _timeline_events(html: str) -> list[dict[str, Any]]:
    """Timeline embarquée en JS dans le panel time. Best-effort explicite."""
    decoder = json.JSONDecoder()
    for match in list(re.finditer(r"\[\s*\{", html))[:20]:
        try:
            data, _ = decoder.raw_decode(html[match.start() :])
        except ValueError:
            continue
        if not isinstance(data, list):
            continue
        events: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict) or "name" not in item:
                events = []
                break
            duration = item.get("duration")
            if duration is None and isinstance(item.get("periods"), list):
                duration = sum(
                    (p.get("end", 0) - p.get("start", 0))
                    for p in item["periods"]
                    if isinstance(p, dict)
                )
            events.append(
                {
                    "name": item.get("name"),
                    "category": item.get("category"),
                    "duration_ms": round(duration, 3)
                    if isinstance(duration, int | float)
                    else None,
                }
            )
        if events:
            return events[:LIST_LIMIT]
    return []


def _parse_logger(html: str) -> dict[str, Any]:
    metrics = _metrics(html)
    return {
        "errors": _metric_int(metrics, "error"),
        "warnings": _metric_int(metrics, "warning"),
        "deprecations": _metric_int(metrics, "deprecation"),
    }


_PARSERS = {
    "db": _parse_db,
    "twig": _parse_twig,
    "cache": _parse_cache,
    "exception": _parse_exception,
    "http_client": _parse_http_client,
    "messenger": _parse_messenger,
    "router": _parse_router,
    "time": _parse_time,
    "logger": _parse_logger,
}
