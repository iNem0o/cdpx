"""SEO: the rendered DOM's contract, assertable in one line."""

from __future__ import annotations

from scripts.site_casts.runtime import Cmd, Comment, Scenario, Shell

SCENARIO = Scenario(
    id="seo",
    title="cdpx — the SEO contract of the rendered DOM, the one Googlebot indexes",
    height=16,
    steps=(
        Comment(
            "only the final DOM is authoritative for Googlebot rendering — not the served HTML"
        ),
        Cmd(
            argv=("seo", "{base}/seo.html"),
            expect=(
                '"title":"SEO fixture — compliant page"',
                '"images_without_alt":0',
                '"findings":[]',
            ),
        ),
        Comment("the contract is JSON: jq -e turns it into a CI gate"),
        Shell(
            command="cdpx seo {base}/seo.html | jq -c '.findings'",
            expect=("[]",),
        ),
        Comment("the same command on a broken page names what's missing"),
        Shell(
            command="cdpx seo {base}/seo-broken.html | jq -c '.findings'",
            expect=(
                "missing title",
                "missing meta description",
                "missing canonical",
                "2 h1 (expected: 1)",
                "2 image(s) without alt",
            ),
        ),
    ),
)
