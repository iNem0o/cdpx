"""Catalogue hiérarchique et curaté de la rubrique Docs."""

from pathlib import Path

from cdpx.proofing.documentation import build_documentation_catalog


def test_real_documentation_catalog_publishes_references_and_all_features():
    catalog = build_documentation_catalog()

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
    assert len(feature_docs) == 8
    assert all(item["feature_id"] for item in feature_docs)
    assert all(item["html"].lstrip().startswith("<h2") for item in feature_docs)

    session = next(
        item for item in catalog["documents"] if item["path"] == "docs/SESSION-LIFECYCLE.md"
    )
    assert session["html"].count('<pre class="mermaid">') == 4
    assert "#/docs/view/HARNESS.md" in session["html"]
    assert "#/docs/view/docs/features/state-session.md" in session["html"]


def test_catalog_tree_follows_filesystem_and_applies_labels():
    catalog = build_documentation_catalog()
    tree = catalog["tree"]

    assert tree["label"] == "Documentation produit"
    docs = next(child for child in tree["children"] if child["path"] == "docs")
    assert docs["label"] == "Références"
    features = next(child for child in docs["children"] if child["path"] == "docs/features")
    assert features["label"] == "Spécifications fonctionnelles"
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
    _write_config(tmp_path, "../secret.md")

    catalog = build_documentation_catalog(root=tmp_path, feature_specs=[])

    assert catalog["documents"] == []
    assert "hors dépôt" in catalog["violations"][0]


def test_catalog_rejects_empty_glob(tmp_path):
    _write_config(tmp_path, "docs/missing-*.md")

    catalog = build_documentation_catalog(root=tmp_path, feature_specs=[])

    assert catalog["documents"] == []
    assert "sans résultat" in catalog["violations"][0]


def test_catalog_rejects_symlinked_document(tmp_path):
    target = tmp_path / "README.md"
    target.write_text("# README\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "linked.md").symlink_to(target)
    _write_config(tmp_path, "docs/linked.md")

    catalog = build_documentation_catalog(root=tmp_path, feature_specs=[])

    assert catalog["documents"] == []
    assert "régulier requis" in catalog["violations"][0]
