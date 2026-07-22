"""Symfony profiler: the real reference app's WebProfiler becomes data.

Requires the tests/symfony-app app reachable from the host —
`./dev site-record` starts it via the docker-compose.site-casts.yml overlay
(dynamic loopback port) and passes `--symfony-base` to the generator. Without a
base provided, the scenario is skipped cleanly.
"""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario, Shell

SCENARIO = Scenario(
    id="profiler",
    title="cdpx — the Symfony WebProfiler becomes data",
    requires="symfony",
    steps=(
        Comment("the reference Symfony app runs in dev: the WebProfiler traces every request"),
        Comment("healthy variant: 3 Doctrine queries, no duplicates"),
        Cmd(
            argv=("profiler", "{symfony}/scenario/profiler/doctrine-normal", "--panels", "db"),
            expect=('"duplicates":0',),
            timeout=60.0,
        ),
        Comment("same page, N+1 version: the db panel quantifies the drift"),
        Cmd(
            argv=(
                "profiler",
                "{symfony}/scenario/profiler/doctrine-n-plus-one",
                "--panels",
                "db,cache",
            ),
            expect=('"queries":6', '"duplicates":4'),
            timeout=60.0,
        ),
        Comment("the same one-liner, dropped into CI, becomes a merge gate"),
        Shell(
            command=(
                "cdpx profiler {symfony}/scenario/profiler/doctrine-n-plus-one --panels db "
                "| jq -e '.panels.db.duplicates == 0' >/dev/null "
                '|| echo "N+1 detected — merge refused"'
            ),
            expect=("N+1 detected — merge refused",),
            timeout=60.0,
        ),
    ),
)
