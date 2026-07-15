"""Rendu CommonMark sûr et extension Mermaid du cockpit."""

from cdpx.proofing.markdown import markdown_title, render_markdown


def test_render_markdown_supports_commonmark_tables_and_anchors():
    """Le rendu couvre le CommonMark dont les docs du dépôt ont besoin
    (listes ordonnées, citations, tables, code inline) et transforme les
    titres accentués en ancres ASCII navigables."""
    source = """# Référence

1. premier
2. second

> note importante

| Option | Effet |
| --- | --- |
| `--pretty` | JSON indenté |
"""

    rendered = render_markdown(source)

    #: chaque construction attendue par les docs survit au rendu, et le titre
    #: accentué reçoit une ancre ASCII stable pour les liens profonds
    assert '<h1 id="reference">Référence</h1>' in rendered
    assert "<ol>" in rendered and "<li>second</li>" in rendered
    assert "<blockquote>" in rendered and "note importante" in rendered
    assert "<table>" in rendered and "<code>--pretty</code>" in rendered
    #: le titre est extractible sans balisage pour nommer le document au cockpit
    assert markdown_title(source) == "Référence"


def test_render_markdown_assigns_stable_duplicate_heading_ids():
    """Deux titres identiques reçoivent des ids distincts et prévisibles:
    les liens d'ancre restent univoques même dans un document répétitif."""
    rendered = render_markdown("## État\n\n## État")

    #: le doublon est suffixé de façon déterministe au lieu d'écraser la première ancre
    assert '<h2 id="etat">' in rendered
    assert '<h2 id="etat-2">' in rendered


def test_render_markdown_mermaid_fence_is_text_only_and_other_fences_stay_code():
    """Une fence mermaid devient un bloc texte échappé que le cockpit rendra
    en diagramme, sans ouvrir de vecteur d'injection; les autres fences
    restent des blocs de code ordinaires."""
    source = """```mermaid
flowchart LR
  A[<script>] --> B
```

```bash
cdpx session status
```
"""

    rendered = render_markdown(source)

    #: le diagramme est livré en texte intégralement échappé — même une balise
    #: script dans un libellé ne peut pas s'exécuter
    assert '<pre class="mermaid">' in rendered
    assert "A[&lt;script&gt;] --&gt; B" in rendered
    #: les autres langages gardent le rendu code standard, non interprété
    assert '<pre><code class="lang-bash">cdpx session status\n</code></pre>' in rendered


def test_render_markdown_disables_raw_html_and_unsafe_links():
    """Un document markdown hostile ne peut pas exécuter de script dans le
    cockpit: le HTML brut est neutralisé par échappement et un lien au schéma
    dangereux perd son href."""
    rendered = render_markdown('<script>alert("x")</script>\n\n[x](javascript:alert(1))')

    #: la balise injectée est affichée comme texte, jamais interprétée
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    #: le libellé du lien survit mais le schéma dangereux ne devient jamais cliquable
    assert "javascript:" in rendered
    assert 'href="javascript:' not in rendered


def test_render_markdown_rewrites_catalog_links_and_disables_excluded_paths():
    """Les liens d'un document sont triés en trois destins: route interne du
    cockpit si la cible est au catalogue, ouverture externe sécurisée sinon,
    et désactivation explicite pour les docs volontairement exclues."""
    rendered = render_markdown(
        "[session](docs/SESSION-LIFECYCLE.md#Cycle-de-vie) "
        "[externe](https://example.com) [todo](docs/TODO.md)",
        source_path="README.md",
        catalog_paths={"README.md", "docs/SESSION-LIFECYCLE.md"},
    )

    #: le lien catalogue devient une route interne, ancre normalisée en slug
    assert 'href="#/docs/view/docs/SESSION-LIFECYCLE.md?section=cycle-de-vie"' in rendered
    #: le lien externe s'ouvre dans un nouvel onglet sans fuite d'opener ni de referrer
    assert 'href="https://example.com" target="_blank" rel="noopener noreferrer"' in rendered
    #: la doc hors catalogue est marquée indisponible plutôt que de produire un lien mort
    assert 'class="doc-link-unavailable"' in rendered
    assert 'href="docs/TODO.md"' not in rendered


def test_render_markdown_full_document_structure():
    """L'ordre des blocs du source est préservé dans le HTML: une fiche Usage
    garde sa structure titre, sous-commande, prose puis exemple."""
    doc = "## Usage\n\n### `cdpx demo`\n\nDescription.\n\n```bash\ncdpx demo\n```\n"
    rendered = render_markdown(doc)

    #: la hiérarchie h2 → h3 → paragraphe → bloc de code suit l'ordre d'écriture
    assert rendered.index("<h2 ") < rendered.index("<h3 ") < rendered.index("<p>")
    assert rendered.index("<p>") < rendered.index("<pre>")
