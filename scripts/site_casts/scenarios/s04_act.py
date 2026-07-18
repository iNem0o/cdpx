"""Agir comme un utilisateur: type --secret-env, click, key, dom-diff."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="act",
    title="cdpx — agir comme un utilisateur, prouver l'effet",
    env={"FORM_NAME": "Ada", "FORM_NAME_2": "Grace"},
    steps=(
        Cmd(argv=("goto", "{base}/form.html"), expect=('"ok":true',)),
        Comment("le secret vient de l'environnement — jamais d'argv, jamais d'un journal"),
        Cmd(
            argv=("type", "#name", "--secret-env", "FORM_NAME", "--clear"),
            expect=('"value_masked":true',),
        ),
        Cmd(argv=("click", "#submit-btn"), expect=('"clicked":"#submit-btn"',)),
        Cmd(argv=("text", "#result"), expect=("OK:Ada",)),
        Comment("key : valider au clavier, comme un utilisateur"),
        Cmd(
            argv=("type", "#name", "--secret-env", "FORM_NAME_2", "--clear"),
            expect=('"value_masked":true',),
        ),
        Cmd(argv=("key", "Enter"), expect=('"pressed":"Enter"',)),
        Cmd(argv=("text", "#result"), expect=("OK:Grace",)),
        Comment("dom-diff : voir exactement ce qu'un clic a changé dans le DOM"),
        Cmd(argv=("goto", "{base}/form.html"), expect=('"ok":true',)),
        Cmd(
            argv=("dom-diff", "--", "click", "#submit-btn"),
            display='cdpx dom-diff -- click "#submit-btn"',
            expect=('"changed":true',),
        ),
    ),
)
