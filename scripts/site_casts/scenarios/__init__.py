"""Registre ordonné des scénarios tutoriels de la homepage."""

from __future__ import annotations

from scripts.site_casts.runtime import Scenario

from . import (
    s01_session,
    s02_nav,
    s03_read,
    s04_act,
    s05_observe,
    s06_capture,
    s07_state,
    s08_seo,
    s09_perf,
    s10_journey,
    s11_resilience,
    s12_profiler,
)

ALL_SCENARIOS: tuple[Scenario, ...] = (
    s01_session.SCENARIO,
    s02_nav.SCENARIO,
    s03_read.SCENARIO,
    s04_act.SCENARIO,
    s05_observe.SCENARIO,
    s06_capture.SCENARIO,
    s07_state.SCENARIO,
    s08_seo.SCENARIO,
    s09_perf.SCENARIO,
    s10_journey.SCENARIO,
    s11_resilience.SCENARIO,
    s12_profiler.SCENARIO,
)


def by_id(identifier: str) -> Scenario:
    for scenario in ALL_SCENARIOS:
        if scenario.id == identifier:
            return scenario
    raise KeyError(identifier)
