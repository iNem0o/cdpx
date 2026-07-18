"""Résilience: intercept (panne simulée) et emulate (mobile, réseau lent)."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="resilience",
    title="cdpx — reproduire l'irreproductible : pannes et mobiles",
    steps=(
        Comment("« que devient la page si l'API tombe ? » — la panne devient un cas de test"),
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
        Comment("le fallback d'erreur se lit dans le DOM — sans toucher à l'infra"),
        Comment("emulate mobile : viewport et UA, le temps d'une action"),
        Cmd(
            argv=("emulate", "mobile", "--", "goto", "{base}/index.html"),
            display="cdpx emulate mobile -- goto {base}/index.html",
            expect=('"preset":"mobile"', '"applied":true'),
        ),
        Comment("emulate slow-3g : 400ms de latence réseau, mesurés sur l'action elle-même"),
        Cmd(
            argv=("emulate", "slow-3g", "--", "goto", "{base}/index.html"),
            display="cdpx emulate slow-3g -- goto {base}/index.html",
            expect=('"preset":"slow-3g"', '"applied":true'),
        ),
    ),
)
