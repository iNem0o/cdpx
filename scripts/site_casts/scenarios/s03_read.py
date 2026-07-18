"""Lire la page au bon niveau: text, html, count, eval, frame."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="read",
    title="cdpx — lire la page au bon niveau d'abstraction",
    steps=(
        Cmd(argv=("goto", "{base}/form.html"), expect=('"ok":true',)),
        Comment("text : la vision sémantique low-cost — bien moins cher qu'un screenshot"),
        Cmd(argv=("text", "h1"), expect=("Formulaire",)),
        Comment("html : la structure quand les attributs comptent (classes, data-*)"),
        Cmd(argv=("html", "#result"), expect=('data-state=\\"idle\\"',)),
        Comment("count : l'assertion la moins chère du monde"),
        Cmd(argv=("count", "input"), expect=('"count":2',)),
        Comment("eval : l'échappatoire universelle — dernier recours, fragile et non typé"),
        Cmd(argv=("eval", "document.title"), expect=('"value":"Fixture: formulaire"',)),
        Comment("frame : lire dans une iframe same-origin (paiement, consentement)"),
        Cmd(argv=("goto", "{base}/iframe.html"), expect=('"ok":true',)),
        Cmd(argv=("frame", "#child-marker"), expect=('"text":"Contenu de l\'iframe"',)),
    ),
)
