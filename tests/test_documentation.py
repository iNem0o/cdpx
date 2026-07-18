"""Hierarchical, curated catalog of the Docs section."""

from pathlib import Path

import pytest

from cdpx.proofing.documentation import build_documentation_catalog


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.publish-feature-proof",
    proves=["The shipped Docs catalog publishes references and feature sheets without violation."],
)
def test_real_documentation_catalog_publishes_references_and_all_features():
    """The catalog built from the real repository publishes the root
    references and every feature sheet in HTML, without violation, with
    internal links rewritten to the cockpit router."""
    catalog = build_documentation_catalog()

    #: the shipped catalog respects its schema, names its index, and
    #: contains no residual violation
    assert catalog["schema"] == "cdpx.docs/v1"
    assert catalog["index"] == "README.md"
    assert catalog["violations"] == []
    paths = {document["path"] for document in catalog["documents"]}
    assert {
        "README.md",
        "HARNESS.md",
        "docs/SESSION-LIFECYCLE.md",
        "docs/PRIMITIVES.md",
        "docs/VALIDATION.md",
    } <= paths
    feature_docs = [item for item in catalog["documents"] if item["kind"] == "feature"]
    #: every feature sheet of the repository is published as body-only HTML
    #: (no front-matter) and stays attached to its feature_id
    assert len(feature_docs) == 8
    assert all(item["feature_id"] for item in feature_docs)
    assert all(item["html"].lstrip().startswith("<h2") for item in feature_docs)

    session = next(
        item for item in catalog["documents"] if item["path"] == "docs/SESSION-LIFECYCLE.md"
    )
    #: mermaid diagrams survive rendering as native blocks and relative
    #: links are rewritten to the cockpit's internal navigation
    assert session["html"].count('<pre class="mermaid">') == 4
    assert "#/docs/view/HARNESS.md" in session["html"]
    assert "#/docs/view/docs/features/state-session.md" in session["html"]


def test_catalog_tree_follows_filesystem_and_applies_labels():
    """The navigation tree reflects the repository's real file layout while
    substituting the human-readable curated labels."""
    catalog = build_documentation_catalog()
    tree = catalog["tree"]

    #: every filesystem level receives its human label, and the eight
    #: sheets are filed under the feature specifications node
    assert tree["label"] == "Product documentation"
    docs = next(child for child in tree["children"] if child["path"] == "docs")
    assert docs["label"] == "References"
    features = next(child for child in docs["children"] if child["path"] == "docs/features")
    assert features["label"] == "Feature specifications"
    assert len(features["documents"]) == 8


def _write_config(root: Path, include: str) -> None:
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "cockpit.toml").write_text(
        f'''schema = "cdpx.docs/v1"
index = "README.md"
include = ["{include}"]
exclude = []
''',
        encoding="utf-8",
    )


def test_catalog_rejects_paths_outside_repository(tmp_path):
    """An include pointing outside the repository is rejected outright: the
    Docs section cannot serve as an exfiltration channel for external
    files."""
    _write_config(tmp_path, "../secret.md")

    catalog = build_documentation_catalog(root=tmp_path, feature_specs=[])

    #: nothing is published and the violation explains the rejection reason
    #: (path escaping the repository root)
    assert catalog["documents"] == []
    assert "path outside repository forbidden" in catalog["violations"][0]


def test_catalog_rejects_empty_glob(tmp_path):
    """A config glob with no match is a violation, not silence: a dead
    entry in cockpit.toml must be seen immediately."""
    _write_config(tmp_path, "docs/missing-*.md")

    catalog = build_documentation_catalog(root=tmp_path, feature_specs=[])

    #: the catalog stays empty and the violation reports the pattern with
    #: no result instead of publishing a misleading subset
    assert catalog["documents"] == []
    assert "include with no results" in catalog["violations"][0]


def test_catalog_rejects_symlinked_document(tmp_path):
    """A document reached through a symlink is rejected: only a regular
    file from the repository is publishable, against symlink escapes."""
    target = tmp_path / "README.md"
    target.write_text("# README\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "linked.md").symlink_to(target)
    _write_config(tmp_path, "docs/linked.md")

    catalog = build_documentation_catalog(root=tmp_path, feature_specs=[])

    #: the symlink is rejected with a message requiring a regular file,
    #: even though its target lives inside the repository
    assert catalog["documents"] == []
    assert "regular document required" in catalog["violations"][0]
