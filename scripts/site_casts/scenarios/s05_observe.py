"""Observabilité: console, network, metrics — les sens de l'agent."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="observe",
    title="cdpx — console, réseau, métriques : les sens de l'agent",
    steps=(
        Comment("un front cassé se voit d'abord en console — encore faut-il la lire"),
        Cmd(argv=("goto", "{base}/console.html"), expect=('"ok":true',)),
        Cmd(
            argv=("console", "--duration", "2"),
            expect=('"errors":2', "fixture-uncaught"),
            timeout=30.0,
        ),
        Comment("network : naviguer en capturant XHR, statuts et poids"),
        Cmd(
            argv=("network", "{base}/network.html", "--settle", "1"),
            expect=('"errors_4xx_5xx":1', '"status":500'),
            timeout=60.0,
        ),
        Comment("metrics : objectiver une dérive — nœuds DOM, heap, layouts"),
        Cmd(argv=("metrics",), expect=('"Nodes":52', '"JSHeapUsedSize"')),
    ),
)
