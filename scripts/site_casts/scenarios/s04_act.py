"""Acting like a user: type --secret-env, click, key, dom-diff."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="act",
    title="cdpx — act like a user, prove the effect",
    env={"FORM_NAME": "Ada", "FORM_NAME_2": "Grace"},
    steps=(
        Cmd(argv=("goto", "{base}/form.html"), expect=('"ok":true',)),
        Comment("the secret comes from the environment — never argv, never a log"),
        Cmd(
            argv=("type", "#name", "--secret-env", "FORM_NAME", "--clear"),
            expect=('"value_masked":true',),
        ),
        Cmd(argv=("click", "#submit-btn"), expect=('"clicked":"#submit-btn"',)),
        Cmd(argv=("text", "#result"), expect=("OK:Ada",)),
        Comment("key: validate with the keyboard, like a user"),
        Cmd(
            argv=("type", "#name", "--secret-env", "FORM_NAME_2", "--clear"),
            expect=('"value_masked":true',),
        ),
        Cmd(argv=("key", "Enter"), expect=('"pressed":"Enter"',)),
        Cmd(argv=("text", "#result"), expect=("OK:Grace",)),
        Comment("dom-diff: see exactly what a click changed in the DOM"),
        Cmd(argv=("goto", "{base}/form.html"), expect=('"ok":true',)),
        Cmd(
            argv=("dom-diff", "--", "click", "#submit-btn"),
            display='cdpx dom-diff -- click "#submit-btn"',
            expect=('"changed":true',),
        ),
    ),
)
