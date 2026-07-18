"""État: cookies et storage, valeurs masquées par défaut."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="state",
    title="cdpx — cookies et storage : l'état, sans les valeurs",
    env={"CONSENT_VALUE": "opt-in-2026-krx"},
    forbidden=("opt-in-2026-krx",),
    steps=(
        Cmd(argv=("goto", "{base}/storage.html"), expect=('"ok":true',)),
        Comment("valeurs masquées par défaut : un agent recopie ses sorties dans son contexte"),
        Cmd(argv=("storage",), expect=('"cdpx-key":"***"', '"values_masked":true')),
        Cmd(
            argv=("storage", "--kind", "session"),
            expect=('"cdpx-session":"***"', '"values_masked":true'),
        ),
        Cmd(argv=("cookies", "get"), expect=('"name":"jsCookie","value":"***"',)),
        Comment("préparer un état de scénario sans exposer la valeur"),
        Cmd(
            argv=(
                "cookies",
                "set",
                "--name",
                "consent",
                "--value-env",
                "CONSENT_VALUE",
                "--url",
                "{base}/",
            ),
            expect=('"name":"consent"', '"success":true'),
        ),
        Cmd(argv=("cookies", "get"), expect=('"name":"consent","value":"***"', '"count":2')),
        Comment("et repartir propre"),
        Cmd(argv=("cookies", "clear"), expect=('"cleared":true', "Storage.clearCookies")),
    ),
)
