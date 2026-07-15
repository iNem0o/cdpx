"""Rendu CommonMark sûr et extension Mermaid du cockpit."""

from cdpx.proofing.markdown import markdown_title, render_markdown


def test_render_markdown_supports_commonmark_tables_and_anchors():
    source = """# Référence

1. premier
2. second

> note importante

| Option | Effet |
| --- | --- |
| `--pretty` | JSON indenté |
"""

    rendered = render_markdown(source)

    assert '<h1 id="reference">Référence</h1>' in rendered
    assert "<ol>" in rendered and "<li>second</li>" in rendered
    assert "<blockquote>" in rendered and "note importante" in rendered
    assert "<table>" in rendered and "<code>--pretty</code>" in rendered
    assert markdown_title(source) == "Référence"


def test_render_markdown_assigns_stable_duplicate_heading_ids():
    rendered = render_markdown("## État\n\n## État")

    assert '<h2 id="etat">' in rendered
    assert '<h2 id="etat-2">' in rendered


def test_render_markdown_mermaid_fence_is_text_only_and_other_fences_stay_code():
    source = """```mermaid
flowchart LR
  A[<script>] --> B
```

```bash
cdpx session status
```
"""

    rendered = render_markdown(source)

    assert '<pre class="mermaid">' in rendered
    assert "A[&lt;script&gt;] --&gt; B" in rendered
    assert '<pre><code class="lang-bash">cdpx session status\n</code></pre>' in rendered


def test_render_markdown_disables_raw_html_and_unsafe_links():
    rendered = render_markdown('<script>alert("x")</script>\n\n[x](javascript:alert(1))')

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "javascript:" in rendered
    assert 'href="javascript:' not in rendered


def test_render_markdown_rewrites_catalog_links_and_disables_excluded_paths():
    rendered = render_markdown(
        "[session](docs/SESSION-LIFECYCLE.md#Cycle-de-vie) "
        "[externe](https://example.com) [todo](docs/TODO.md)",
        source_path="README.md",
        catalog_paths={"README.md", "docs/SESSION-LIFECYCLE.md"},
    )

    assert 'href="#/docs/view/docs/SESSION-LIFECYCLE.md?section=cycle-de-vie"' in rendered
    assert 'href="https://example.com" target="_blank" rel="noopener noreferrer"' in rendered
    assert 'class="doc-link-unavailable"' in rendered
    assert 'href="docs/TODO.md"' not in rendered


def test_render_markdown_full_document_structure():
    doc = "## Usage\n\n### `cdpx demo`\n\nDescription.\n\n```bash\ncdpx demo\n```\n"
    rendered = render_markdown(doc)

    assert rendered.index("<h2 ") < rendered.index("<h3 ") < rendered.index("<p>")
    assert rendered.index("<p>") < rendered.index("<pre>")
