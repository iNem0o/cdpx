"""Resilience: intercept (simulated outage) and emulate (mobile, slow network)."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="resilience",
    title="cdpx — reproducing the irreproducible: outages and mobile",
    steps=(
        Comment(
            "“what happens to the page if the API goes down?” — the outage becomes a test case"
        ),
        Cmd(
            argv=(
                "intercept",
                "--rule",
                "*api* => 503",
                "--settle",
                "1",
                "--",
                "goto",
                "{base}/intercept.html",
            ),
            display='cdpx intercept --rule "*api* => 503" --settle 1 -- goto {base}/intercept.html',
            expect=('"rules":["*api* => 503"]', '"count":6'),
            timeout=60.0,
        ),
        Cmd(
            argv=("text", "#intercept-result"),
            expect=("/api/json:503|/api/status/500:503|/api/slow?ms=120:503|/api/echo:503",),
        ),
        Comment("the error fallback reads straight from the DOM — no need to touch the infra"),
        Comment("emulate mobile: viewport and UA, for the duration of an action"),
        Cmd(
            argv=("emulate", "mobile", "--", "goto", "{base}/index.html"),
            display="cdpx emulate mobile -- goto {base}/index.html",
            expect=('"preset":"mobile"', '"applied":true'),
        ),
        Comment("emulate slow-3g: 400ms of network latency, measured on the action itself"),
        Cmd(
            argv=("emulate", "slow-3g", "--", "goto", "{base}/index.html"),
            display="cdpx emulate slow-3g -- goto {base}/index.html",
            expect=('"preset":"slow-3g"', '"applied":true'),
        ),
    ),
)
