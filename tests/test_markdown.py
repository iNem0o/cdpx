"""Le convertisseur Markdown des fiches features: subset strict, escape-first.

Tout ce qui n'est pas dans cette table de cas n'est PAS supporté — étendre le
convertisseur = ajouter un cas ici d'abord.
"""

import pytest

from cdpx.proofing.markdown import render_markdown

CASES = [
    ("paragraphe", "Bonjour le monde.", "<p>Bonjour le monde.</p>"),
    ("h2", "## Usage", "<h2>Usage</h2>"),
    ("h3", "### `cdpx goto`", "<h3><code>cdpx goto</code></h3>"),
    ("h4", "#### Options", "<h4>Options</h4>"),
    (
        "liste",
        "- premier\n- second",
        "<ul>\n<li>premier</li>\n<li>second</li>\n</ul>",
    ),
    (
        "gras-et-code",
        "**important** et `inline`",
        "<p><strong>important</strong> et <code>inline</code></p>",
    ),
    (
        "lien",
        "[la fiche](docs/features/browser-navigation.md)",
        '<p><a href="docs/features/browser-navigation.md">la fiche</a></p>',
    ),
    (
        "fence-avec-langue",
        "```bash\ncdpx goto http://demo.test/\n```",
        '<pre><code class="lang-bash">cdpx goto http://demo.test/</code></pre>',
    ),
    (
        "fence-echappe-html",
        '```json\n{"html": "<b>x</b>"}\n```',
        '<pre><code class="lang-json">'
        "{&quot;html&quot;: &quot;&lt;b&gt;x&lt;/b&gt;&quot;}</code></pre>",
    ),
    (
        "tableau",
        "| Option | Effet |\n| --- | --- |\n| `--pretty` | JSON indenté |",
        "<table><thead><tr><th>Option</th><th>Effet</th></tr></thead>"
        "<tbody><tr><td><code>--pretty</code></td><td>JSON indenté</td></tr></tbody></table>",
    ),
    (
        "injection-echappee",
        '<script>alert("x")</script>',
        "<p>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;</p>",
    ),
    (
        "paragraphe-multiligne",
        "ligne un\nligne deux\n\nautre",
        "<p>ligne un ligne deux</p>\n<p>autre</p>",
    ),
]


@pytest.mark.parametrize("case_id,source,expected", CASES, ids=[c[0] for c in CASES])
def test_render_markdown_subset(case_id, source, expected):
    assert render_markdown(source) == expected


def test_render_markdown_full_document_structure():
    doc = "## Usage\n\n### `cdpx demo`\n\nDescription.\n\n```bash\ncdpx demo\n```\n\n- point\n"
    html = render_markdown(doc)
    assert html.index("<h2>") < html.index("<h3>") < html.index("<p>") < html.index("<pre>")
    assert html.endswith("</ul>")
