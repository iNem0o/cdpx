"""Performance: vitals sur interaction réelle, coverage du code mort."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario, Shell

SCENARIO = Scenario(
    id="perf",
    title="cdpx — Core Vitals sur vrai clic, code mort mesuré",
    steps=(
        Comment("l'INP se mesure sur un vrai clic dispatché par le domaine Input"),
        Cmd(
            argv=("vitals", "{base}/vitals.html", "--click", "#inp-button", "--settle", "1"),
            expect=('"lcp":', '"cls":', '"inp":'),
            timeout=60.0,
        ),
        Comment("coverage : le poids du JS et du CSS jamais exécutés, par fichier"),
        Cmd(
            argv=("coverage", "{base}/coverage.html"),
            expect=('"unused_bytes":56', '"css":{"rules":1,"used":1,"unused":0}'),
            timeout=60.0,
        ),
        Comment("un budget perf en jq -e devient une porte de merge : exit 1, la PR attend"),
        Shell(
            command=(
                'cdpx vitals {base}/vitals.html --click "#inp-button" --settle 1 '
                "| jq -e '.lcp < 2500 and .cls < 0.1' >/dev/null && echo \"budget perf: OK\""
            ),
            expect=("budget perf: OK",),
            timeout=60.0,
        ),
    ),
)
