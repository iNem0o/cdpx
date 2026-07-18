"""Observability: console, network, metrics — the agent's senses."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="observe",
    title="cdpx — console, network, metrics: the agent's senses",
    steps=(
        Comment("a broken frontend shows up in the console first — you still have to read it"),
        Cmd(argv=("goto", "{base}/console.html"), expect=('"ok":true',)),
        Cmd(
            argv=("console", "--duration", "2"),
            expect=('"errors":2', "fixture-uncaught"),
            timeout=30.0,
        ),
        Comment("network: navigate while capturing XHR, statuses and weight"),
        Cmd(
            argv=("network", "{base}/network.html", "--settle", "1"),
            expect=('"errors_4xx_5xx":1', '"status":500'),
            timeout=60.0,
        ),
        Comment("metrics: objectify a drift — DOM nodes, heap, layouts"),
        Cmd(argv=("metrics",), expect=('"Nodes":52', '"JSHeapUsedSize"')),
    ),
)
