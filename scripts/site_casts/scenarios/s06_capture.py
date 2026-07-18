"""Capture: screenshot, pdf, a11y — pixels et sémantique."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="capture",
    title="cdpx — capturer : pixels, PDF, et la page en rôles et noms",
    steps=(
        Cmd(argv=("goto", "{base}/form.html"), expect=('"ok":true',)),
        Comment("la vision pixel, quand le texte ne suffit pas (bug CSS, rendu)"),
        Cmd(
            argv=("screenshot", "-o", "etat.png"),
            expect=('"format":"png"', '"full_page":false'),
        ),
        Cmd(
            argv=("screenshot", "-o", "page.jpg", "--full-page", "--format", "jpeg"),
            expect=('"format":"jpeg"', '"full_page":true'),
        ),
        Comment("pdf : figer la page en preuve imprimable"),
        Cmd(argv=("pdf", "-o", "page.pdf"), expect=('"bytes":23691',)),
        Comment("a11y : l'arbre d'accessibilité — le screenshot sémantique, pour trois fois rien"),
        Cmd(
            argv=("a11y",),
            expect=("RootWebArea", '"role":"textbox","name":"Nom"'),
        ),
    ),
)
