"""Panel-specific Symfony profiler parsers."""

from __future__ import annotations

import json
import re
import urllib.parse
from collections import Counter
from html import unescape
from html.parser import HTMLParser
from typing import Any

from cdpx.security import redact_text, redact_url

from .constants import LIST_LIMIT
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
    """Parses a known panel; captures content errors into ``parse_error``.

    A key missing from the catalog remains a call error and raises
    ValueError.
    """
    from .catalog import PANEL_SPECS_BY_KEY

    spec = PANEL_SPECS_BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"unknown panel: {key}")
    if status != 200 or not html:
        return {"available": False, "status": status}
    try:
        parsed = spec.parser(html)
    except Exception as e:  # noqa: BLE001 - contract: never a parse exception
        return {
            "available": True,
            "parse_error": redact_text(f"{type(e).__name__}: {e}"),
        }
    return {"available": True, **parsed}


_FQCN_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:\\[A-Z][A-Za-z0-9_]*)+\b")


# -- per-panel parsers -----------------------------------------------------------


def _parse_db(html: str) -> dict[str, Any]:
    metrics = _metrics(html)
    queries = _metric_int(metrics, "database queries")
    statements = _metric_int(metrics, "different statements")
    out: dict[str, Any] = {
        "queries": queries,
        "statements": statements,
        "duplicates": max(0, queries - statements),
        "max_repetitions": 0,
        "repeated": [],
        "time_ms": _ms(_metric(metrics, "query time")),
        "list": [],
        "tagged_total": 0,
        "tagged_truncated": False,
        "tagged": [],
    }
    table = _find_table(_tables(html), "info")
    if table:
        details = _db_row_details(html)
        sql_col = _column(table, "info")
        time_col = _column(table, "time")
        count_col = _column(table, "count")
        parsed_rows: list[dict[str, Any]] = []
        frequencies: Counter[str] = Counter()
        tagged_total = 0
        for row_index, row in enumerate(table["rows"]):
            if sql_col is None or sql_col >= len(row):
                continue
            # The Info cell contains the SQL followed by the parameter dump.
            sql = re.split(r"\s+Parameters\b", row[sql_col])[0].strip()
            entry: dict[str, Any] = {"sql": redact_text(sql)}
            count = 1
            if count_col is not None and count_col < len(row):
                count = _int(row[count_col]) or 1
            if count > 1:
                entry["count"] = count
            if time_col is not None and time_col < len(row):
                entry["duration_ms"] = _float(row[time_col])
            if len(parsed_rows) < LIST_LIMIT:
                parsed_rows.append(entry)
            frequencies[entry["sql"]] += count
            detail = details[row_index] if row_index < len(details) else {}
            tags = _leading_sql_tags(sql, detail.get("comments", []))
            if tags:
                tagged_total += 1
                if len(out["tagged"]) < LIST_LIMIT:
                    tagged: dict[str, Any] = {
                        "tags": [redact_text(tag) for tag in tags],
                        "sql": entry["sql"],
                        "count": count,
                    }
                    if "duration_ms" in entry:
                        tagged["duration_ms"] = entry["duration_ms"]
                    source = _query_source(detail.get("frames", []))
                    if source is not None:
                        tagged["source"] = source
                    out["tagged"].append(tagged)
        out["list"] = parsed_rows
        repeated = sorted(
            ((count, sql) for sql, count in frequencies.items() if count > 1),
            key=lambda item: (-item[0], item[1]),
        )
        out["repeated"] = [{"sql": sql, "count": count} for count, sql in repeated[:LIST_LIMIT]]
        out["max_repetitions"] = max(frequencies.values(), default=0)
        out["tagged_total"] = tagged_total
        out["tagged_truncated"] = tagged_total > len(out["tagged"])
    return out


def _db_row_details(html: str) -> list[dict[str, Any]]:
    """Extracts Shopware's highlighted comments and hidden query backtraces."""
    starts = list(re.finditer(r'<tr\s+id="queryNo-[^"]+"', html))
    details: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(html)
        chunk = html[start.start() : end]
        pre = re.search(r'<pre[^>]*class="[^"]*highlight-sql[^"]*"[^>]*>(.*?)</pre>', chunk, re.S)
        comments: list[str] = []
        if pre:
            comments = [
                _norm(unescape(re.sub(r"<[^>]+>", "", value)))
                for value in re.findall(
                    r'<span[^>]*class="[^"]*\bcomment\b[^"]*"[^>]*>(.*?)</span>',
                    pre.group(1),
                    re.S,
                )
            ]
        frames: list[dict[str, Any]] = []
        trace = re.search(r'<div\s+id="backtrace-[^"]+"[^>]*>(.*?)</div>', chunk, re.S)
        if trace:
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", trace.group(1), re.S):
                anchor = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', row, re.S)
                if anchor is None or "status-warning" not in anchor.group(2):
                    continue
                call = _norm(unescape(re.sub(r"<[^>]+>", "", anchor.group(2))))
                line_match = re.search(r"\(line\s+(\d+)\)\s*$", call)
                line = int(line_match.group(1)) if line_match else None
                call = re.sub(r"\s*\(line\s+\d+\)\s*$", "", call)
                frame: dict[str, Any] = {"call": redact_text(call)}
                file = _file_from_profiler_link(unescape(anchor.group(1)))
                if file:
                    frame["file"] = redact_text(file)
                if line is not None:
                    frame["line"] = line
                frames.append(frame)
        details.append({"comments": comments, "frames": frames})
    return details


def _file_from_profiler_link(href: str) -> str | None:
    line_suffix = re.search(r"[?&]line=\d+", href)
    without_line = href[: line_suffix.start()] if line_suffix else href
    parsed = urllib.parse.urlsplit(without_line)
    query_file = urllib.parse.parse_qs(parsed.query).get("file", [])
    if query_file:
        return query_file[0]
    if parsed.scheme == "file" and parsed.path:
        return urllib.parse.unquote(parsed.path)
    return None


def _leading_sql_tags(sql: str, comments: list[str]) -> list[str]:
    remaining = _norm(sql)
    tags: list[str] = []
    for comment in comments:
        normalized = _norm(comment)
        if not normalized or not remaining.startswith(normalized):
            break
        remaining = remaining[len(normalized) :].lstrip()
        if normalized.startswith("--"):
            value = normalized[2:].strip()
        elif normalized.startswith("#"):
            value = normalized[1:].strip()
        elif normalized.startswith("/*") and normalized.endswith("*/"):
            value = normalized[2:-2].strip()
        else:
            break
        if value:
            tags.append(value)
    return tags


def _query_source(frames: list[dict[str, Any]]) -> dict[str, Any] | None:
    for frame in frames:
        file = frame.get("file")
        if isinstance(file, str) and "/vendor/" not in file.replace("\\", "/"):
            return frame
    for frame in frames:
        call = frame.get("call")
        if isinstance(call, str) and not call.startswith(
            ("Doctrine\\DBAL\\", "Symfony\\Bridge\\Doctrine\\")
        ):
            return frame
    return frames[0] if frames else None


def _parse_shopware_rules(html: str) -> dict[str, Any]:
    table = _find_table(_tables(html), "priority", "name", "id")
    rows = table["rows"] if table else []
    parsed: list[dict[str, Any]] = []
    for row in rows[:LIST_LIMIT]:
        if len(row) < 5:
            continue
        parsed.append(
            {
                "priority": _int(row[0]),
                "name": redact_text(row[1]),
                "id": redact_text(row[2]),
                "module_types": [
                    redact_text(item.strip()) for item in row[3].split(",") if item.strip()
                ],
                "description": redact_text(row[4]),
            }
        )
    return {"count": len(rows), "list": parsed}


_CACHE_CALLER_RE = re.compile(r"(\d+)\s*x\s+(.*?)(?=\s*\d+\s*x\s+|$)")


def _parse_shopware_cache_tags(html: str) -> dict[str, Any]:
    tables = [table for table in _tables(html) if _find_table([table], "tag", "source")]
    routes: set[str] = set()
    tag_count = 0
    emissions = 0
    items: list[dict[str, Any]] = []
    for table in tables:
        route = redact_text(table["heading"])
        if route:
            routes.add(route)
        for row in table["rows"]:
            if len(row) < 2:
                continue
            tag_count += 1
            caller_matches = _CACHE_CALLER_RE.findall(row[1])
            callers: list[dict[str, Any]] = [
                {"call": redact_text(call.strip()), "count": int(count)}
                for count, call in caller_matches[:LIST_LIMIT]
            ]
            emissions += sum(int(count) for count, _call in caller_matches)
            if len(items) < LIST_LIMIT:
                items.append(
                    {
                        "route": route,
                        "tag": redact_text(row[0]),
                        "callers_total": len(caller_matches),
                        "callers_truncated": len(caller_matches) > LIST_LIMIT,
                        "callers": callers,
                    }
                )
    return {
        "routes": len(routes),
        "tags": tag_count,
        "emissions": emissions,
        "list": items,
    }


_SEMANTIC_TABLE_CLASSES = {
    "feature-flags": "feature_flags",
    "cart-line-item-table": "cart_line_items",
    "collectors-table": "collectors",
    "processors-table": "processors",
}


class _SemanticTableParser(HTMLParser):
    """Reads only bounded-output Shopware tables and skips Cart dump rows."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self._depth = 0
        self._ignored_at: int | None = None
        self._table_at: int | None = None
        self._table_role: str | None = None
        self._row_at: int | None = None
        self._skip_row_at: int | None = None
        self._cell_at: int | None = None
        self._cell_text: list[str] = []
        self._cell_icon: bool | None = None
        self._row_cells: list[str] = []
        self._row_icons: list[bool | None] = []
        self._row_decorators: list[dict[str, Any]] = []
        self._row_is_header = True
        self._section: str | None = None
        self._row_section: str | None = None
        self._chip_at: int | None = None
        self._chip_text: list[str] = []
        self._chip_priority: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._depth += 1
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag in ("script", "style") and self._ignored_at is None:
            self._ignored_at = self._depth
            return
        if self._ignored_at is not None:
            return
        if self._skip_row_at is not None:
            return
        if tag == "table" and self._table_at is None:
            role = next(
                (
                    _SEMANTIC_TABLE_CLASSES[name]
                    for name in classes
                    if name in _SEMANTIC_TABLE_CLASSES
                ),
                None,
            )
            if role is not None:
                self._table_at = self._depth
                self._table_role = role
                self.tables.setdefault(role, [])
            return
        if self._table_at is None:
            return
        if tag in ("thead", "tbody", "tfoot"):
            self._section = tag
            return
        if tag == "tr" and self._row_at is None:
            if "code-block-row" in classes:
                self._skip_row_at = self._depth
                return
            self._row_at = self._depth
            self._row_cells = []
            self._row_icons = []
            self._row_decorators = []
            self._row_is_header = True
            self._row_section = self._section
            return
        if self._row_at is None:
            return
        if tag in ("td", "th") and self._cell_at is None:
            self._cell_at = self._depth
            self._cell_text = []
            self._cell_icon = None
            if tag == "td":
                self._row_is_header = False
            return
        if self._cell_at is None:
            return
        icon_ref = " ".join(
            value
            for name, value in attrs
            if value is not None and name in ("id", "href", "xlink:href")
        )
        if "icons-solid-checkmark" in icon_ref:
            self._cell_icon = True
        elif "icons-solid-x" in icon_ref:
            self._cell_icon = False
        if tag == "span" and "chip" in classes and self._chip_at is None:
            self._chip_at = self._depth
            self._chip_text = []
            self._chip_priority = _int(attributes.get("title"))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_at is not None:
            if self._ignored_at == self._depth and tag in ("script", "style"):
                self._ignored_at = None
            self._depth = max(0, self._depth - 1)
            return
        if self._skip_row_at is not None:
            if self._skip_row_at == self._depth and tag == "tr":
                self._skip_row_at = None
            self._depth = max(0, self._depth - 1)
            return
        if self._chip_at == self._depth and tag == "span":
            service_id = _norm("".join(self._chip_text))
            if service_id:
                self._row_decorators.append(
                    {"service_id": service_id, "priority": self._chip_priority}
                )
            self._chip_at = None
        if self._cell_at == self._depth and tag in ("td", "th"):
            self._row_cells.append(_norm("".join(self._cell_text)))
            self._row_icons.append(self._cell_icon)
            self._cell_at = None
        if self._row_at == self._depth and tag == "tr":
            if self._row_section != "thead" and self._table_role is not None:
                self.tables[self._table_role].append(
                    {
                        "cells": self._row_cells,
                        "icons": self._row_icons,
                        "decorators": self._row_decorators,
                    }
                )
            self._row_at = None
            self._row_section = None
        if tag in ("thead", "tbody", "tfoot"):
            self._section = None
        if self._table_at == self._depth and tag == "table":
            self._table_at = None
            self._table_role = None
            self._section = None
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_at is not None or self._skip_row_at is not None:
            return
        if self._cell_at is not None:
            self._cell_text.append(data)
        if self._chip_at is not None:
            self._chip_text.append(data)


def _semantic_tables(html: str) -> dict[str, list[dict[str, Any]]]:
    parser = _SemanticTableParser()
    parser.feed(html)
    return parser.tables


def _parse_shopware_feature_flags(html: str) -> dict[str, Any]:
    rows = _semantic_tables(html).get("feature_flags", [])
    items: list[dict[str, Any]] = []
    active = 0
    for row in rows:
        cells = row["cells"]
        icons = row["icons"]
        is_active = icons[3] if len(icons) > 3 else None
        if is_active is True:
            active += 1
        if len(items) >= LIST_LIMIT or len(cells) < 5:
            continue
        items.append(
            {
                "name": redact_text(cells[0]),
                "active": is_active,
                "default": icons[2] if len(icons) > 2 else None,
                "major": icons[1] if len(icons) > 1 else None,
                "description": redact_text(cells[4]),
            }
        )
    return {
        "count": len(rows),
        "active": active,
        "truncated": len(rows) > len(items),
        "list": items,
    }


def _redacted_display(value: str) -> str:
    return redact_text(value)


def _parse_shopware_cart(html: str) -> dict[str, Any]:
    tables = _semantic_tables(html)
    metrics = _metrics(html)
    item_metric = _metric(metrics, "items", "cart")
    total_metric = _metric(metrics, "cart total")
    item_count = _int(item_metric["value"]) if item_metric else None
    present = "no cart available" not in html.lower() and bool(
        item_metric or "cart contains no items" in html.lower() or tables.get("cart_line_items")
    )

    line_rows: list[dict[str, Any]] = []
    subtotal_display: str | None = None
    shipping_display: str | None = None
    total_display = _redacted_display(total_metric["value"]) if total_metric else None
    taxes: list[dict[str, Any]] = []
    taxes_total = 0
    for row in tables.get("cart_line_items", []):
        cells = row["cells"]
        quantity = _int(cells[0]) if cells else None
        if len(cells) >= 5 and quantity is not None:
            line_rows.append(
                {
                    "quantity": quantity,
                    "label": redact_text(cells[1]),
                    "type": redact_text(cells[2]),
                    "unit_price_display": _redacted_display(cells[3]),
                    "total_price_display": _redacted_display(cells[4]),
                }
            )
            continue
        if len(cells) < 2:
            continue
        label = cells[0].strip().lower()
        amount = _redacted_display(cells[-1])
        if label == "subtotal":
            subtotal_display = amount
        elif label == "shipping":
            shipping_display = amount
        elif label == "total":
            total_display = amount
        else:
            rate_match = re.search(r"(-?\d+(?:[.,]\d+)?)\s*%", cells[0])
            if rate_match:
                taxes_total += 1
                if len(taxes) < LIST_LIMIT:
                    taxes.append(
                        {
                            "rate": float(rate_match.group(1).replace(",", ".")),
                            "amount_display": amount,
                        }
                    )

    parsed_item_total = len(line_rows)
    line_total = item_count if item_count is not None else parsed_item_total
    safe_item_count = item_count if item_count is not None else parsed_item_total
    totals: dict[str, Any] = {}
    if subtotal_display is not None:
        totals["subtotal_display"] = subtotal_display
    if shipping_display is not None:
        totals["shipping_display"] = shipping_display
    if total_display is not None:
        totals["total_display"] = total_display
    totals.update(
        {
            "taxes_total": taxes_total,
            "taxes_truncated": taxes_total > len(taxes),
            "taxes": taxes,
        }
    )

    return {
        "present": present,
        "item_count": safe_item_count if present else 0,
        "totals": totals,
        "line_items": {
            "total": line_total if present else 0,
            "truncated": bool(present and line_total > min(parsed_item_total, LIST_LIMIT)),
            "items": line_rows[:LIST_LIMIT],
        },
        "pipeline": {
            "collectors": _parse_cart_services(tables.get("collectors", [])),
            "processors": _parse_cart_services(tables.get("processors", [])),
        },
    }


def _parse_cart_services(rows: list[dict[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for row in rows[:LIST_LIMIT]:
        cells = row["cells"]
        if len(cells) < 2:
            continue
        decorators = row["decorators"]
        cleaned_decorators = [
            {
                "service_id": redact_text(item["service_id"]),
                "priority": item["priority"],
            }
            for item in decorators[:LIST_LIMIT]
        ]
        items.append(
            {
                "service_id": redact_text(cells[0]),
                "priority": _int(cells[1]),
                "decorated_by_total": len(decorators),
                "decorated_by_truncated": len(decorators) > len(cleaned_decorators),
                "decorated_by": cleaned_decorators,
            }
        )
    return {
        "total": len(rows),
        "truncated": len(rows) > len(items),
        "items": items,
    }


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
                # The cell concatenates name + path: keep the first token.
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
        # Pools are h3 tabs under the "Pools" h2; the h3 title carries
        # the service name followed by a numeric badge ("app.scenario_pool 5").
        name = re.sub(r"\s+\d+$", "", metric["h3"]).strip()
        if metric in totals or not name or "." not in name:
            continue
        pool = out["pools"].setdefault(
            name,
            {"calls": 0, "reads": 0, "hits": 0, "misses": 0, "writes": 0, "deletes": 0},
        )
        # Exact match: the "Hits/reads" ratio metric must not overwrite
        # either hits or reads.
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
    # Class: abbr[title] from the exception hierarchy. Watch out for global
    # classes (\RuntimeException): no backslash, so not an FQCN.
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
# Header of an http_client trace: <th><span class="http-method">GET</span></th><th>url</th>
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
    # Status looked up WITHIN each trace's segment (between two trace
    # headers): the page's #summary banner also carries a
    # status-response-status-code (that of the profiled request), and a
    # trace that timed out has no status at all.
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
    # A dispatched message = one <table class="message-item">. Inside it,
    # the Message/Envelope tabs repeat the Bus row: count only the first
    # one per block.
    buses: dict[str, int] = {}
    classes: list[str] = []
    chunks = re.split(r'<table class="message-item"', html)[1:]
    for chunk in chunks:
        bus_match = re.search(r"<th[^>]*>\s*Bus\s*</th>\s*<td[^>]*>\s*([^<]+?)\s*</td>", chunk)
        if bus_match:
            bus = bus_match.group(1)
            buses[bus] = buses.get(bus, 0) + 1
        # Message FQCN: title attribute of the dump ("App\Message\X NN characters"),
        # fall back to the first FQCN in the block.
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
    """Cleans up a value dumped by Sfdump: quotes and dump noise."""
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
    # Status: profiler #summary banner (present on every panel page).
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
    """Timeline embedded as JS in the time panel. Explicit best-effort."""
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
    # The logger panel has no metric blocks: the counts live in the filter
    # labels, e.g. `Errors <span class="badge ...">2</span>`.
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
