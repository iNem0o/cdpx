"""Stable semantic panel catalog for composable profiler adapters."""

from .adapters import PanelSpec, panel_specs
from .constants import LIST_LIMIT

PANEL_SPECS: tuple[PanelSpec, ...] = panel_specs()
PANEL_SPECS_BY_KEY: dict[str, PanelSpec] = {spec.key: spec for spec in PANEL_SPECS}
PANEL_SOURCE_CANDIDATES: dict[str, tuple[str, ...]] = {
    spec.key: spec.collectors for spec in PANEL_SPECS
}
PANEL_SOURCES: dict[str, str] = {spec.key: spec.collectors[0] for spec in PANEL_SPECS}
ALL_PANELS: tuple[str, ...] = tuple(spec.key for spec in PANEL_SPECS)
DEFAULT_PANELS: tuple[str, ...] = tuple(spec.key for spec in PANEL_SPECS if spec.default)

__all__ = [
    "ALL_PANELS",
    "DEFAULT_PANELS",
    "LIST_LIMIT",
    "PANEL_SOURCE_CANDIDATES",
    "PANEL_SOURCES",
    "PANEL_SPECS",
    "PANEL_SPECS_BY_KEY",
    "PanelSpec",
]
