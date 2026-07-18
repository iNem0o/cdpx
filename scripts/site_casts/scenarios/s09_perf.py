"""Performance: vitals on a real interaction, dead-code coverage."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario, Shell

SCENARIO = Scenario(
    id="perf",
    title="cdpx — Core Vitals on a real click, dead code measured",
    steps=(
        Comment("INP is measured on a real click dispatched by the Input domain"),
        Cmd(
            argv=("vitals", "{base}/vitals.html", "--click", "#inp-button", "--settle", "1"),
            expect=('"lcp":', '"cls":', '"inp":'),
            timeout=60.0,
        ),
        Comment("coverage: the weight of JS and CSS never executed, per file"),
        Cmd(
            argv=("coverage", "{base}/coverage.html"),
            expect=('"unused_bytes":56', '"css":{"rules":1,"used":1,"unused":0}'),
            timeout=60.0,
        ),
        Comment("a perf budget in jq -e becomes a merge gate: exit 1, the PR waits"),
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
