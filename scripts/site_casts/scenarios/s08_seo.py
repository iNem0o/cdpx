"""SEO: le contrat du DOM rendu, assertable en une ligne."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario, Shell

SCENARIO = Scenario(
    id="seo",
    title="cdpx — le contrat SEO du DOM rendu, celui que Googlebot indexe",
    height=16,
    steps=(
        Comment("seul le DOM final fait foi côté rendering Googlebot — pas le HTML servi"),
        Cmd(
            argv=("seo", "{base}/seo.html"),
            expect=(
                '"title":"Fixture SEO — page conforme"',
                '"images_without_alt":0',
                '"findings":[]',
            ),
        ),
        Comment("le contrat est un JSON : jq -e en fait une porte de CI"),
        Shell(
            command="cdpx seo {base}/seo.html | jq -c '.findings'",
            expect=("[]",),
        ),
        Comment("la même commande sur une page cassée nomme les manques"),
        Shell(
            command="cdpx seo {base}/seo-broken.html | jq -c '.findings'",
            expect=(
                "title manquant",
                "meta description manquante",
                "canonical manquant",
                "2 h1 (attendu: 1)",
                "2 image(s) sans alt",
            ),
        ),
    ),
)
