"""Session supervisée: identité triple, autorité, teardown."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario

SCENARIO = Scenario(
    id="session",
    title="cdpx — la session supervisée : un Chrome jetable, une identité, un teardown",
    height=16,
    manage_session=False,
    steps=(
        Comment(
            "une session = un Chrome dédié au run : profil neuf, loopback, origines allowlistées"
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
            "la triple identité (session, run, target) est installée — chaque commande la vérifie"
        ),
        Cmd(argv=("session", "status"), expect=('"run_id":"demo"', '"authority":"privileged"')),
        Comment("jamais votre Chrome personnel : on inspecte le navigateur réellement attribué"),
        Cmd(argv=("version",), expect=('"Browser":"Chrome/',)),
        Comment("tabs list vérifie l'origine réelle du tab attribué — il faut d'abord y naviguer"),
        Cmd(argv=("goto", "{base}/index.html"), expect=('"ok":true',)),
        Cmd(argv=("tabs", "list"), expect=('"count":1',)),
        Comment("à l'arrêt, le superviseur détruit profil, artefacts et processus"),
        Cmd(argv=("session", "stop"), expect=('"run_id":"demo"', '"stopped":true')),
    ),
)
