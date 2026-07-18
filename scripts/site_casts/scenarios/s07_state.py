"""State: cookies and storage, values redacted by default."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="state",
    title="cdpx — cookies and storage: the state, without the values",
    env={"CONSENT_VALUE": "opt-in-2026-krx"},
    forbidden=("opt-in-2026-krx",),
    steps=(
        Cmd(argv=("goto", "{base}/storage.html"), expect=('"ok":true',)),
        Comment("redacted by default: an agent copies its outputs straight into its context"),
        Cmd(argv=("storage",), expect=('"cdpx-key":"***"', '"values_masked":true')),
        Cmd(
            argv=("storage", "--kind", "session"),
            expect=('"cdpx-session":"***"', '"values_masked":true'),
        ),
        Cmd(argv=("cookies", "get"), expect=('"name":"jsCookie","value":"***"',)),
        Comment("prepare a scenario's state without exposing the value"),
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
        Comment("and leave things clean"),
        Cmd(argv=("cookies", "clear"), expect=('"cleared":true', "Storage.clearCookies")),
    ),
)
