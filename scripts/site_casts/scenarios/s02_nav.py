"""Navigation et synchronisation: goto, wait, l'état réellement prêt."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="nav",
    title="cdpx — naviguer et attendre le vrai état de la page",
    steps=(
        Comment("le site témoin du dépôt sert des pages déterministes sur {base}"),
        Cmd(argv=("goto", "{base}/index.html"), expect=('"ok":true',)),
        Cmd(argv=("count", "nav a"), expect=('"count":8',)),
        Cmd(argv=("text", "#intro"), expect=("Site témoin déterministe",)),
        Comment("sur une SPA, le load event ment : ce contenu n'arrive que 300 ms après"),
        Cmd(argv=("goto", "{base}/spa.html"), expect=('"ok":true',)),
        Cmd(
            argv=("--timeout", "5", "wait", "#late-content"),
            display='cdpx --timeout 5 wait "#late-content"',
            expect=('"found":true',),
        ),
        Cmd(argv=("text", "#late-content"), expect=("Contenu arrivé après 300ms",)),
        Comment("l'attente est explicite : l'agent n'asserte jamais un état intermédiaire"),
    ),
)
