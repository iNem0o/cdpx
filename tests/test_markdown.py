"""Safe CommonMark rendering and the cockpit's Mermaid extension."""

from cdpx.proofing.markdown import markdown_title, render_markdown


def test_render_markdown_supports_commonmark_tables_and_anchors():
    """Rendering covers the CommonMark the repository's docs need (ordered
    lists, blockquotes, tables, inline code) and turns accented titles into
    navigable ASCII anchors."""
    source = """# Référence

1. first
2. second

> important note

| Option | Effect |
| --- | --- |
| `--pretty` | indented JSON |
"""

    rendered = render_markdown(source)

    #: every construct the docs expect survives rendering, and the accented
    #: title receives a stable ASCII anchor for deep links
    assert '<h1 id="reference">Référence</h1>' in rendered
    assert "<ol>" in rendered and "<li>second</li>" in rendered
    assert "<blockquote>" in rendered and "important note" in rendered
    assert "<table>" in rendered and "<code>--pretty</code>" in rendered
    #: the title is extractable without markup to name the document in the cockpit
    assert markdown_title(source) == "Référence"


def test_render_markdown_assigns_stable_duplicate_heading_ids():
    """Two identical titles receive distinct, predictable ids: anchor links
    stay unambiguous even in a repetitive document."""
    rendered = render_markdown("## État\n\n## État")

    #: the duplicate is suffixed deterministically instead of overwriting the first anchor
    assert '<h2 id="etat">' in rendered
    assert '<h2 id="etat-2">' in rendered


def test_render_markdown_mermaid_fence_is_text_only_and_other_fences_stay_code():
    """A mermaid fence becomes an escaped text block that the cockpit will
    render as a diagram, without opening an injection vector; other fences
    remain ordinary code blocks."""
    source = """```mermaid
flowchart LR
  A[<script>] --> B
```

```bash
cdpx session status
```
"""

    rendered = render_markdown(source)

    #: the diagram is delivered as fully escaped text — even a script tag
    #: in a label cannot execute
    assert '<pre class="mermaid">' in rendered
    assert "A[&lt;script&gt;] --&gt; B" in rendered
    #: other languages keep the standard, non-interpreted code rendering
    assert '<pre><code class="lang-bash">cdpx session status\n</code></pre>' in rendered


def test_render_markdown_disables_raw_html_and_unsafe_links(evidence_case):
    """A hostile markdown document cannot execute a script in the cockpit:
    raw HTML is neutralized by escaping and a link with a dangerous scheme
    loses its href."""
    rendered = render_markdown('<script>alert("x")</script>\n\n[x](javascript:alert(1))')

    #: the injected tag is displayed as text, never interpreted
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    #: the link label survives but the dangerous scheme never becomes clickable
    assert "javascript:" in rendered
    assert 'href="javascript:' not in rendered

    if evidence_case is not None:
        evidence_case.attach_text(
            "Rendered HTML — escaped script, disarmed javascript link",
            rendered,
            filename="rendered.html",
        )


def test_render_markdown_rewrites_catalog_links_and_disables_excluded_paths():
    """A document's links are sorted into three fates: an internal cockpit
    route if the target is in the catalog, safe external opening otherwise,
    and explicit disabling for docs deliberately excluded."""
    rendered = render_markdown(
        "[session](docs/SESSION-LIFECYCLE.md#Lifecycle) "
        "[external](https://example.com) [missing](docs/MISSING.md)",
        source_path="README.md",
        catalog_paths={"README.md", "docs/SESSION-LIFECYCLE.md"},
    )

    #: the catalog link becomes an internal route, anchor normalized to a slug
    assert 'href="#/docs/view/docs/SESSION-LIFECYCLE.md?section=lifecycle"' in rendered
    #: the external link opens in a new tab with no opener or referrer leak
    assert 'href="https://example.com" target="_blank" rel="noopener noreferrer"' in rendered
    #: the doc outside the catalog is marked unavailable instead of producing a dead link
    assert 'class="doc-link-unavailable"' in rendered
    assert 'href="docs/MISSING.md"' not in rendered


def test_render_markdown_full_document_structure():
    """The source block order is preserved in the HTML: a Usage sheet keeps
    its title, subcommand, prose, then example structure."""
    doc = "## Usage\n\n### `cdpx demo`\n\nDescription.\n\n```bash\ncdpx demo\n```\n"
    rendered = render_markdown(doc)

    #: the h2 → h3 → paragraph → code block hierarchy follows the writing order
    assert rendered.index("<h2 ") < rendered.index("<h3 ") < rendered.index("<p>")
    assert rendered.index("<p>") < rendered.index("<pre>")
