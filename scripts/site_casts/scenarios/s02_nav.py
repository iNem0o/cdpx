"""Navigation and synchronization: goto, wait, the actual ready state."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="nav",
    title="cdpx — navigate and wait for the page's real state",
    steps=(
        Comment("the repo's reference site serves deterministic pages on {base}"),
        Cmd(argv=("goto", "{base}/index.html"), expect=('"ok":true',)),
        Cmd(argv=("count", "nav a"), expect=('"count":8',)),
        Cmd(argv=("text", "#intro"), expect=("Deterministic reference site",)),
        Comment("on an SPA, the load event lies: this content only arrives 300 ms later"),
        Cmd(argv=("goto", "{base}/spa.html"), expect=('"ok":true',)),
        Cmd(
            argv=("--timeout", "5", "wait", "#late-content"),
            display='cdpx --timeout 5 wait "#late-content"',
            expect=('"found":true',),
        ),
        Cmd(argv=("text", "#late-content"), expect=("Content arrived after 300ms",)),
        Comment("the wait is explicit: the agent never asserts on an intermediate state"),
    ),
)
