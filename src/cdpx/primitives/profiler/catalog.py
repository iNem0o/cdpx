"""Stable Symfony profiler panel catalog."""

PANEL_SOURCES: dict[str, str] = {
    "router": "request",
    "time": "time",
    "db": "db",
    "twig": "twig",
    "cache": "cache",
    "exception": "exception",
    "http_client": "http_client",
    "messenger": "messenger",
    "logger": "logger",
}
PANEL_SOURCE_CANDIDATES: dict[str, tuple[str, ...]] = {
    **{key: (source,) for key, source in PANEL_SOURCES.items()},
    # Shopware 6.7 ships its own DBAL profiler collector instead of the
    # DoctrineBundle collector while exposing the same metrics and markup.
    "db": ("db", "app.connection_collector"),
}
ALL_PANELS: tuple[str, ...] = tuple(PANEL_SOURCES)
LIST_LIMIT = 20
