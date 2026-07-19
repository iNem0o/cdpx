"""Capture: screenshot, pdf, a11y — pixels and semantics."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="capture",
    title="cdpx — capture: pixels, PDF, and the page as roles and names",
    steps=(
        Cmd(argv=("goto", "{base}/form.html"), expect=('"ok":true',)),
        Comment("pixel vision, when text isn't enough (CSS bug, rendering)"),
        Cmd(
            argv=("screenshot", "-o", "state.png"),
            expect=('"format":"png"', '"full_page":false'),
        ),
        Cmd(
            argv=("screenshot", "-o", "page.jpg", "--full-page", "--format", "jpeg"),
            expect=('"format":"jpeg"', '"full_page":true'),
        ),
        Comment("pdf: freeze the page as printable proof"),
        Cmd(argv=("pdf", "-o", "page.pdf"), expect=('"bytes":22687',)),
        Comment("a11y: the accessibility tree — the semantic screenshot, for next to nothing"),
        Cmd(
            argv=("a11y",),
            expect=("RootWebArea", '"role":"textbox","name":"Name"'),
        ),
    ),
)
