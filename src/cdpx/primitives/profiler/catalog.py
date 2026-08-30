"""Stable semantic panel catalog for Symfony profiler adapters."""

from .adapters import SYMFONY_PANELS, panel_sources

PANEL_SOURCE_CANDIDATES: dict[str, tuple[str, ...]] = dict(panel_sources())
PANEL_SOURCES: dict[str, str] = {key: candidates[0] for key, candidates in SYMFONY_PANELS}
PANEL_SOURCES.update(
    {
        key: candidates[0]
        for key, candidates in PANEL_SOURCE_CANDIDATES.items()
        if key not in PANEL_SOURCES
    }
)
ALL_PANELS: tuple[str, ...] = tuple(PANEL_SOURCES)
LIST_LIMIT = 20
