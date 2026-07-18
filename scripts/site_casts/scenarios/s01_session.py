"""Supervised session: identity triple, authority, teardown."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="session",
    title="cdpx — the supervised session: a disposable Chrome, one identity, one teardown",
    height=16,
    manage_session=False,
    steps=(
        Comment(
            "a session = a Chrome dedicated to the run: fresh profile, loopback, "
            "allowlisted origins"
        ),
        Cmd(
            argv=(
                "session",
                "start",
                "--run-id",
                "demo",
                "--authority",
                "privileged",
                "--origins",
                "http://127.0.0.1:*",
                "--ttl",
                "900",
                "--export",
            ),
            display=(
                'eval "$(cdpx session start --run-id demo --authority privileged '
                '--origins "http://127.0.0.1:*" --ttl 900 --export)"'
            ),
            capture_exports=True,
            timeout=120.0,
        ),
        Comment(
            "the identity triple (session, run, target) is installed — every command checks it"
        ),
        Cmd(argv=("session", "status"), expect=('"run_id":"demo"', '"authority":"privileged"')),
        Comment("never your personal Chrome: we inspect the browser actually assigned"),
        Cmd(argv=("version",), expect=('"Browser":"Chrome/',)),
        Comment("tabs list checks the assigned tab's real origin — you must navigate there first"),
        Cmd(argv=("goto", "{base}/index.html"), expect=('"ok":true',)),
        Cmd(argv=("tabs", "list"), expect=('"count":1',)),
        Comment("on stop, the supervisor destroys the profile, artifacts and processes"),
        Cmd(argv=("session", "stop"), expect=('"run_id":"demo"', '"stopped":true')),
    ),
)
