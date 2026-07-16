"""Symfony Web Profiler collection and parsing primitives."""

from .catalog import ALL_PANELS, PANEL_SOURCES
from .collection import collect_profiler_report, fetch_panels, normalize_panels
from .parsers import parse_panel

__all__ = [
    "ALL_PANELS",
    "PANEL_SOURCES",
    "collect_profiler_report",
    "fetch_panels",
    "normalize_panels",
    "parse_panel",
]
