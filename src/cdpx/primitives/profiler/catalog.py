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
ALL_PANELS: tuple[str, ...] = tuple(PANEL_SOURCES)
LIST_LIMIT = 20
