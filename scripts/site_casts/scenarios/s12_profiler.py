"""Profiler Symfony: le WebProfiler de la vraie app témoin devient une donnée.

Nécessite l'app Symfony de tests/symfony-app joignable depuis l'hôte —
`make site-casts` la démarre via l'overlay docker-compose.site-casts.yml
(loopback :8025) et passe `--symfony-base` au générateur. Sans base fournie,
le scénario est sauté proprement.
"""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario, Shell

SCENARIO = Scenario(
    id="profiler",
    title="cdpx — le WebProfiler Symfony devient une donnée",
    requires="symfony",
    steps=(
        Comment("l'app Symfony témoin tourne en dev : le WebProfiler trace chaque requête"),
        Comment("variante saine : 3 requêtes Doctrine, aucun doublon"),
        Cmd(
            argv=("profiler", "{symfony}/scenario/profiler/doctrine-normal", "--panels", "db"),
            expect=('"duplicates":0',),
            timeout=60.0,
        ),
        Comment("même page, version N+1 : le panel db chiffre la dérive"),
        Cmd(
            argv=(
                "profiler",
                "{symfony}/scenario/profiler/doctrine-n-plus-one",
                "--panels",
                "db,cache",
            ),
            expect=('"queries":6', '"duplicates":4'),
            timeout=60.0,
        ),
        Comment("le même one-liner, posé en CI, devient une porte de merge"),
        Shell(
            command=(
                "cdpx profiler {symfony}/scenario/profiler/doctrine-n-plus-one --panels db "
                "| jq -e '.panels.db.duplicates == 0' >/dev/null "
                '|| echo "N+1 détecté — merge refusé"'
            ),
            expect=("N+1 détecté — merge refusé",),
            timeout=60.0,
        ),
    ),
)
