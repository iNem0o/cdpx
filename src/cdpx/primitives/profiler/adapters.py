"""Composable Symfony profiler extension catalog."""

from __future__ import annotations

from dataclasses import dataclass

PanelSources = tuple[tuple[str, tuple[str, ...]], ...]

SHOPWARE_RULES_COLLECTOR = (
    "Shopware\\Core\\Profiling\\Subscriber\\ActiveRulesDataCollectorSubscriber"
)
SHOPWARE_CACHE_TAGS_COLLECTOR = "Shopware\\Core\\Profiling\\Subscriber\\CacheTagCollectorSubscriber"
SHOPWARE_CART_COLLECTOR = "Shopware\\Core\\Profiling\\Subscriber\\CartDataCollectorSubscriber"
SHOPWARE_SCRIPT_COLLECTOR = "Shopware\\Core\\Framework\\Script\\Debugging\\ScriptTraces"


@dataclass(frozen=True)
class ProfilerAdapter:
    """An optional profiler flavor selected from advertised collector IDs."""

    name: str
    markers: frozenset[str]
    panels: PanelSources

    def detected(self, collector_ids: set[str]) -> bool:
        return bool(self.markers & collector_ids)


SYMFONY_PANELS: PanelSources = (
    ("router", ("request",)),
    ("time", ("time",)),
    ("db", ("db", "app.connection_collector")),
    ("twig", ("twig",)),
    ("cache", ("cache",)),
    ("exception", ("exception",)),
    ("http_client", ("http_client",)),
    ("messenger", ("messenger",)),
    ("logger", ("logger",)),
)

SHOPWARE_ADAPTER = ProfilerAdapter(
    name="shopware",
    markers=frozenset(
        {
            SHOPWARE_RULES_COLLECTOR,
            SHOPWARE_CACHE_TAGS_COLLECTOR,
            SHOPWARE_CART_COLLECTOR,
            SHOPWARE_SCRIPT_COLLECTOR,
        }
    ),
    panels=(
        ("db", ("app.connection_collector", "db")),
        ("shopware_rules", (SHOPWARE_RULES_COLLECTOR,)),
        ("shopware_cache_tags", (SHOPWARE_CACHE_TAGS_COLLECTOR,)),
    ),
)

PROFILER_ADAPTERS: tuple[ProfilerAdapter, ...] = (SHOPWARE_ADAPTER,)


def detect_extensions(collector_ids: list[str]) -> list[str]:
    """Returns every matching extension; adapters compose instead of branching."""
    available = set(collector_ids)
    return [adapter.name for adapter in PROFILER_ADAPTERS if adapter.detected(available)]


def panel_sources() -> PanelSources:
    """Builds ordered source candidates for every public semantic panel."""
    sources = dict(SYMFONY_PANELS)
    order = [key for key, _candidates in SYMFONY_PANELS]
    for adapter in PROFILER_ADAPTERS:
        for key, candidates in adapter.panels:
            if key not in sources:
                order.append(key)
            existing = sources.get(key, ())
            sources[key] = tuple(dict.fromkeys((*existing, *candidates)))
    return tuple((key, sources[key]) for key in order)
