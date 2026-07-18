"""Reading the page at the right level: text, html, count, eval, frame."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="read",
    title="cdpx — read the page at the right level of abstraction",
    steps=(
        Cmd(argv=("goto", "{base}/form.html"), expect=('"ok":true',)),
        Comment("text: the low-cost semantic view — far cheaper than a screenshot"),
        Cmd(argv=("text", "h1"), expect=("Form",)),
        Comment("html: the structure when attributes matter (classes, data-*)"),
        Cmd(argv=("html", "#result"), expect=('data-state=\\"idle\\"',)),
        Comment("count: the cheapest assertion there is"),
        Cmd(argv=("count", "input"), expect=('"count":2',)),
        Comment("eval: the universal escape hatch — last resort, fragile and untyped"),
        Cmd(argv=("eval", "document.title"), expect=('"value":"Fixture: form"',)),
        Comment("frame: reading inside a same-origin iframe (payment, consent)"),
        Cmd(argv=("goto", "{base}/iframe.html"), expect=('"ok":true',)),
        Cmd(argv=("frame", "#child-marker"), expect=('"text":"Iframe content"',)),
    ),
)
