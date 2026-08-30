"""Symfony Web Profiler collection and parsing primitives."""

from .catalog import ALL_PANELS, DEFAULT_PANELS, PANEL_SOURCES, PANEL_SPECS, PanelSpec
from .collection import collect_profiler_report, fetch_panels, fetch_profiler, normalize_panels
from .parsers import parse_panel

__all__ = [
    "ALL_PANELS",
    "DEFAULT_PANELS",
    "PANEL_SOURCES",
    "PANEL_SPECS",
    "PanelSpec",
    "collect_profiler_report",
    "fetch_panels",
    "fetch_profiler",
    "normalize_panels",
    "parse_panel",
]
