"""Generic tolerant HTML extraction for Symfony profiler pages."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

# -- generic extractors -----------------------------------------------------

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
    """`class="metric"` blocks from the WebProfilerBundle.

    It's the most stable markup across panels (div.metric > span.value +
    span.label, with an optional unit in a nested span.unit). Each metric
    records the last h2/h3/h4 seen (the cache panel arranges its pools under
    h3 tabs, with h4 subtitles). <script>/<style> contents are ignored.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metrics: list[dict[str, str]] = []
        self.headings = {"h2": "", "h3": "", "h4": ""}
        self._depth = 0
        self._skip_at: int | None = None
        self._metric_at: int | None = None
        self._value_at: int | None = None
        self._unit_at: int | None = None
        self._label_at: int | None = None
        self._heading_at: int | None = None
        self._heading_tag = ""
        self._value: list[str] = []
        self._unit: list[str] = []
        self._label: list[str] = []
        self._head: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            return
        self._depth += 1
        if tag in ("script", "style") and self._skip_at is None:
            self._skip_at = self._depth
            return
        if self._skip_at is not None:
            return
        classes = _classes(attrs)
        if tag in ("h2", "h3", "h4") and self._heading_at is None:
            self._heading_at = self._depth
            self._heading_tag = tag
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
        if self._skip_at is not None:
            if self._skip_at == self._depth and tag in ("script", "style"):
                self._skip_at = None
                self._depth = max(0, self._depth - 1)
            return
        if self._unit_at == self._depth:
            self._unit_at = None
        if self._value_at == self._depth:
            self._value_at = None
        if self._label_at == self._depth:
            self._label_at = None
        if self._heading_at == self._depth:
            text = _norm("".join(self._head))
            self.headings[self._heading_tag] = text
            if self._heading_tag == "h2":
                self.headings["h3"] = ""
                self.headings["h4"] = ""
            elif self._heading_tag == "h3":
                self.headings["h4"] = ""
            self._heading_at = None
        if self._metric_at == self._depth:
            self.metrics.append(
                {
                    "label": _norm("".join(self._label)),
                    "value": _norm("".join(self._value)),
                    "unit": _norm("".join(self._unit)),
                    "heading": self.headings["h4"] or self.headings["h3"] or self.headings["h2"],
                    "h2": self.headings["h2"],
                    "h3": self.headings["h3"],
                    "h4": self.headings["h4"],
                }
            )
            self._metric_at = None
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_at is not None:
            return
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
    """Tables associated with the last h2/h3/h4 heading encountered.

    A row is a header row ONLY if all its cells are <th>: the request/
    messenger panels use mixed rows <th>key</th><td>value</td> that are
    data. <script>/<style> contents (Sfdump dumps embedded in cells) are
    ignored.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict[str, Any]] = []
        self._depth = 0
        self._skip_at: int | None = None
        self._heading_at: int | None = None
        self._head: list[str] = []
        self.heading = ""
        self._table_at: int | None = None
        self._cell_at: int | None = None
        self._cell: list[str] = []
        self._row: list[str] = []
        self._row_has_td = False
        self._row_has_th = False
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            return
        self._depth += 1
        if tag in ("script", "style") and self._skip_at is None:
            self._skip_at = self._depth
            return
        if self._skip_at is not None:
            return
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
            self._row_has_td = False
            self._row_has_th = False
        elif tag in ("td", "th") and self._cell_at is None:
            self._cell_at = self._depth
            self._cell = []
            if tag == "th":
                self._row_has_th = True
            else:
                self._row_has_td = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if self._skip_at is not None:
            if self._skip_at == self._depth and tag in ("script", "style"):
                self._skip_at = None
                self._depth = max(0, self._depth - 1)
            return
        if self._heading_at == self._depth:
            self.heading = _norm("".join(self._head))
            self._heading_at = None
        if self._cell_at == self._depth and tag in ("td", "th"):
            self._row.append(_norm("".join(self._cell)))
            self._cell_at = None
        if tag == "tr" and self._current is not None and self._row:
            header_row = self._row_has_th and not self._row_has_td
            if header_row and not self._current["headers"]:
                self._current["headers"] = self._row
            elif not header_row:
                self._current["rows"].append(self._row)
            self._row = []
        if tag == "table" and self._table_at == self._depth:
            if self._current is not None:
                self.tables.append(self._current)
            self._current = None
            self._table_at = None
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_at is not None:
            return
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
    """Panels advertised by the sidebar (?panel=X links). Best-effort cross-check."""
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
    """A time metric's value converted to milliseconds."""
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
    """First table whose header or heading contains all the fragments."""
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
