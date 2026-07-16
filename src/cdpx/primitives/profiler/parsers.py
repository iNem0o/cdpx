"""Panel-specific Symfony profiler parsers."""

from __future__ import annotations

import json
import re
from typing import Any

from cdpx.security import redact_text, redact_url

from .catalog import LIST_LIMIT, PANEL_SOURCES
from .html import (
    _column,
    _find_table,
    _float,
    _int,
    _metric,
    _metric_int,
    _metrics,
    _ms,
    _norm,
    _tables,
)


def parse_panel(key: str, status: int, html: str) -> dict[str, Any]:
    """Parse un panel connu; capture les erreurs de contenu dans ``parse_error``.

    Une clé absente du catalogue reste une erreur d'appel et lève ValueError.
    """
    if key not in PANEL_SOURCES:
        raise ValueError(f"panel inconnu: {key}")
    if status != 200 or not html:
        return {"available": False, "status": status}
    parser = _PARSERS[key]
    try:
        parsed = parser(html)
    except Exception as e:  # noqa: BLE001 - contrat: jamais d'exception de parse
        return {
            "available": True,
            "parse_error": redact_text(f"{type(e).__name__}: {e}"),
        }
    return {"available": True, **parsed}


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
            # La cellule Info contient le SQL puis le dump des paramètres.
            sql = re.split(r"\s+Parameters\b", row[sql_col])[0].strip()
            entry: dict[str, Any] = {"sql": redact_text(sql)}
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
                # La cellule concatène nom + chemin: garder le premier token.
                out["list"].append(row[0].split()[0])
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
        # Les pools sont des onglets h3 sous le h2 "Pools"; le titre h3 porte
        # le nom du service suivi d'un badge numérique ("app.scenario_pool 5").
        name = re.sub(r"\s+\d+$", "", metric["h3"]).strip()
        if metric in totals or not name or "." not in name:
            continue
        pool = out["pools"].setdefault(
            name,
            {"calls": 0, "reads": 0, "hits": 0, "misses": 0, "writes": 0, "deletes": 0},
        )
        # Correspondance exacte: le metric ratio "Hits/reads" ne doit écraser
        # ni hits ni reads.
        label = metric["label"].lower()
        if label in pool:
            value = _int(metric["value"])
            if value is not None:
                pool[label] = value
    return out


def _parse_exception(html: str) -> dict[str, Any]:
    lowered = html.lower()
    if "no exception was thrown" in lowered:
        return {"raised": False, "class": None, "message": None}
    message = None
    match = re.search(r'class="[^"]*exception-message[^"]*"[^>]*>(.*?)</', html, flags=re.DOTALL)
    if match:
        message = redact_text(_norm(re.sub(r"<[^>]+>", " ", match.group(1))))
    # Classe: abbr[title] de la hiérarchie d'exception. Attention aux classes
    # globales (\RuntimeException): pas de backslash, donc pas un FQCN.
    exception_class = None
    abbr_titles = [
        title.strip()
        for title in re.findall(r'<abbr[^>]*title="([^"]+)"', html)
        if re.fullmatch(r"[A-Za-z_][\w\\]*", title.strip())
    ]
    for title in abbr_titles:
        if title.rsplit("\\", 1)[-1].endswith(("Exception", "Error")):
            exception_class = title
            break
    if exception_class is None:
        for candidate in _FQCN_RE.findall(html):
            if candidate.endswith(("Exception", "Error")):
                exception_class = candidate
                break
    if exception_class is None and abbr_titles:
        exception_class = abbr_titles[0]
    return {"raised": True, "class": exception_class, "message": message}


# Statut HTTP rendu par le profiler: <span class="...status-response-status-code...">200</span>
_STATUS_SPAN_RE = re.compile(r'class="[^"]*status-response-status-code[^"]*"[^>]*>\s*(\d{3})')
# En-tête d'une trace http_client: <th><span class="http-method">GET</span></th><th>url</th>
_HTTP_TRACE_RE = re.compile(
    r'<span class="http-method">\s*([A-Z]+)\s*</span>\s*</th>\s*<th[^>]*>\s*([^<\s][^<]*?)\s*<',
    flags=re.DOTALL,
)


def _parse_http_client(html: str) -> dict[str, Any]:
    metrics = _metrics(html)
    requests_count = _metric_int(metrics, "total requests")
    out: dict[str, Any] = {
        "clients": 0,
        "requests": requests_count,
        "errors": 0,
        "list": [],
    }
    # Statut cherché DANS le segment de chaque trace (entre deux en-têtes de
    # trace): le bandeau #summary de la page porte lui aussi un
    # status-response-status-code (celui de la requête profilée) et une trace
    # en timeout n'a pas de statut du tout.
    traces = list(_HTTP_TRACE_RE.finditer(html))[:LIST_LIMIT]
    for idx, match in enumerate(traces):
        entry: dict[str, Any] = {
            "method": match.group(1),
            "url": redact_url(_norm(match.group(2))),
        }
        segment_end = traces[idx + 1].start() if idx + 1 < len(traces) else len(html)
        status_match = _STATUS_SPAN_RE.search(html, match.end(), segment_end)
        if status_match:
            entry["status"] = _int(status_match.group(1))
        out["list"].append(entry)
    statuses = [entry.get("status") for entry in out["list"]]
    out["errors"] = sum(1 for s in statuses if s is not None and s >= 400)
    clients = {
        _norm(re.sub(r"<[^>]+>", " ", m.group(1)))
        for m in re.finditer(r'<h3 class="tab-title">(.*?)</h3>', html, flags=re.DOTALL)
    }
    clients.discard("")
    out["clients"] = len(clients) or (1 if requests_count else 0)
    return out


def _parse_messenger(html: str) -> dict[str, Any]:
    # Un message dispatché = une <table class="message-item">. À l'intérieur,
    # les onglets Message/Envelope répètent la rangée Bus: ne compter que la
    # première par bloc.
    buses: dict[str, int] = {}
    classes: list[str] = []
    chunks = re.split(r'<table class="message-item"', html)[1:]
    for chunk in chunks:
        bus_match = re.search(r"<th[^>]*>\s*Bus\s*</th>\s*<td[^>]*>\s*([^<]+?)\s*</td>", chunk)
        if bus_match:
            bus = bus_match.group(1)
            buses[bus] = buses.get(bus, 0) + 1
        # FQCN du message: attribut title du dump ("App\Message\X NN characters"),
        # repli sur le premier FQCN du bloc.
        title = re.search(r'title="((?:[A-Za-z_]\w*\\)+\w+) \d+ characters"', chunk)
        fqcn = title.group(1) if title else None
        if fqcn is None:
            fallback = _FQCN_RE.search(chunk)
            fqcn = fallback.group(0) if fallback else None
        if fqcn and len(classes) < LIST_LIMIT:
            classes.append(fqcn)
    dispatched = len(chunks)
    no_handler = html.lower().count("no handler")
    return {
        "dispatched": dispatched,
        "handled": max(0, dispatched - no_handler),
        "buses": buses,
        "list": [{"class": c} for c in classes],
    }


def _strip_dump(value: str) -> str:
    """Nettoie une valeur dumpée par Sfdump: guillemets et bruit de dump."""
    return value.strip().strip('"').strip()


def _parse_router(html: str) -> dict[str, Any]:
    route = None
    controller_cell = None
    for table in _tables(html):
        for row in table["rows"]:
            if len(row) < 2:
                continue
            key = row[0].strip()
            if key == "_route" and route is None:
                route = _strip_dump(row[1])
            elif key == "_controller" and controller_cell is None:
                controller_cell = row[1]
    controller = None
    if controller_cell:
        fqcn = _FQCN_RE.search(controller_cell)
        if fqcn:
            controller = fqcn.group(0)
            method = re.search(r'"(\w+)"', controller_cell[fqcn.end() :])
            if "::" not in controller_cell and method:
                controller = f"{controller}::{method.group(1)}"
    # Statut: bandeau #summary du profiler (présent sur chaque page panel).
    status_match = _STATUS_SPAN_RE.search(html)
    status_code = _int(status_match.group(1)) if status_match else None
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
    # Le panel logger n'a pas de blocs metric: les comptes vivent dans les
    # libellés de filtres, ex. `Errors <span class="badge ...">2</span>`.
    counts = {"errors": 0, "warnings": 0, "deprecations": 0}
    for label, key in (
        ("Errors", "errors"),
        ("Warnings", "warnings"),
        ("Deprecations", "deprecations"),
    ):
        match = re.search(label + r'\s*<span class="badge[^"]*">\s*(\d+)', html, flags=re.DOTALL)
        if match:
            counts[key] = int(match.group(1))
    return counts


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
