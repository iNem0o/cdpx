"""Composable Symfony profiler panel and extension catalog."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from .parsers import (
    _parse_cache,
    _parse_db,
    _parse_exception,
    _parse_http_client,
    _parse_logger,
    _parse_messenger,
    _parse_router,
    _parse_shopware_cache_tags,
    _parse_shopware_cart,
    _parse_shopware_feature_flags,
    _parse_shopware_rules,
    _parse_time,
    _parse_twig,
)

PanelParser = Callable[[str], dict[str, Any]]

SHOPWARE_RULES_COLLECTOR = (
    "Shopware\\Core\\Profiling\\Subscriber\\ActiveRulesDataCollectorSubscriber"
)
SHOPWARE_CACHE_TAGS_COLLECTOR = "Shopware\\Core\\Profiling\\Subscriber\\CacheTagCollectorSubscriber"
SHOPWARE_CART_COLLECTOR = "Shopware\\Core\\Profiling\\Subscriber\\CartDataCollectorSubscriber"
SHOPWARE_FEATURE_FLAGS_COLLECTOR = "feature_flag"
SHOPWARE_SCRIPT_COLLECTOR = "Shopware\\Core\\Framework\\Script\\Debugging\\ScriptTraces"


@dataclass(frozen=True)
class PanelSpec:
    """One semantic output panel backed by advertised collector candidates."""

    key: str
    collectors: tuple[str, ...]
    default: bool
    parser: PanelParser


@dataclass(frozen=True)
class ProfilerAdapter:
    """An optional profiler flavor selected from advertised collector IDs."""

    name: str
    markers: frozenset[str]
    panels: tuple[PanelSpec, ...]

    def detected(self, collector_ids: set[str]) -> bool:
        return bool(self.markers & collector_ids)


SYMFONY_PANEL_SPECS: tuple[PanelSpec, ...] = (
    PanelSpec("router", ("request",), True, _parse_router),
    PanelSpec("time", ("time",), True, _parse_time),
    PanelSpec("db", ("db", "app.connection_collector"), True, _parse_db),
    PanelSpec("twig", ("twig",), True, _parse_twig),
    PanelSpec("cache", ("cache",), True, _parse_cache),
    PanelSpec("exception", ("exception",), True, _parse_exception),
    PanelSpec("http_client", ("http_client",), True, _parse_http_client),
    PanelSpec("messenger", ("messenger",), True, _parse_messenger),
    PanelSpec("logger", ("logger",), True, _parse_logger),
)

SHOPWARE_ADAPTER = ProfilerAdapter(
    name="shopware",
    markers=frozenset(
        {
            SHOPWARE_RULES_COLLECTOR,
            SHOPWARE_CACHE_TAGS_COLLECTOR,
            SHOPWARE_CART_COLLECTOR,
            SHOPWARE_FEATURE_FLAGS_COLLECTOR,
            SHOPWARE_SCRIPT_COLLECTOR,
        }
    ),
    panels=(
        PanelSpec("db", ("app.connection_collector", "db"), True, _parse_db),
        PanelSpec("shopware_rules", (SHOPWARE_RULES_COLLECTOR,), True, _parse_shopware_rules),
        PanelSpec(
            "shopware_cache_tags",
            (SHOPWARE_CACHE_TAGS_COLLECTOR,),
            True,
            _parse_shopware_cache_tags,
        ),
        PanelSpec(
            "shopware_feature_flags",
            (SHOPWARE_FEATURE_FLAGS_COLLECTOR,),
            True,
            _parse_shopware_feature_flags,
        ),
        PanelSpec("shopware_cart", (SHOPWARE_CART_COLLECTOR,), False, _parse_shopware_cart),
    ),
)

PROFILER_ADAPTERS: tuple[ProfilerAdapter, ...] = (SHOPWARE_ADAPTER,)


def detect_extensions(collector_ids: list[str]) -> list[str]:
    """Returns every matching extension; adapters compose instead of branching."""
    available = set(collector_ids)
    return [adapter.name for adapter in PROFILER_ADAPTERS if adapter.detected(available)]


def panel_specs() -> tuple[PanelSpec, ...]:
    """Merges adapter candidates without changing semantic keys or parsers."""
    specs = {spec.key: spec for spec in SYMFONY_PANEL_SPECS}
    order = [spec.key for spec in SYMFONY_PANEL_SPECS]
    for adapter in PROFILER_ADAPTERS:
        for contribution in adapter.panels:
            existing = specs.get(contribution.key)
            if existing is None:
                specs[contribution.key] = contribution
                order.append(contribution.key)
                continue
            specs[contribution.key] = replace(
                existing,
                collectors=tuple(dict.fromkeys((*existing.collectors, *contribution.collectors))),
                default=existing.default or contribution.default,
            )
    return tuple(specs[key] for key in order)
