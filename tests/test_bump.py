"""The mechanical release preparation (``./dev bump``) moves every pin.

The tool is exercised against a miniature worktree rebuilt from the shared
pin registry, so these tests hold exactly when the registry and the
rewriter agree — the same alignment ``test_packaging`` asserts on the real
tree.
"""

import datetime
from pathlib import Path

import pytest
from tools.bump import bump, stamped_changelog
from tools.release_pins import version_pins

OLD = "0.1.2"
NEW = "0.1.3"

CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Something worth releasing.

## [0.1.2] — 2026-07-21

### Added

- Prior notes.
"""


def make_tree(root: Path, version: str = OLD) -> None:
    for source, tokens in version_pins(version).items():
        path = root / source
        path.parent.mkdir(parents=True, exist_ok=True)
        if source == "pyproject.toml":
            path.write_text(f'[project]\nname = "cdpx"\nversion = "{version}"\n', encoding="utf-8")
        elif source == "site/index.html":
            #: reproduce the real homepage, where both pin tokens overlap in
            #: one sentence ("Version X.Y.Z · pre-1.0 beta.")
            path.write_text(
                f'<span class="badge beta">{version} · pre-1.0 beta</span>\n'
                f"<strong>Version {version} · pre-1.0 beta.</strong>\n",
                encoding="utf-8",
            )
        else:
            path.write_text("\n".join(tokens) + "\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")


def test_bump_moves_every_pin_and_stamps_the_changelog(tmp_path, monkeypatch):
    make_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    bump(NEW)
    for source, tokens in version_pins(NEW).items():
        text = (tmp_path / source).read_text(encoding="utf-8")
        for token in tokens:
            assert token in text, f"{source}: pin not moved to {NEW}"
    changelog = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    today = datetime.date.today().isoformat()
    assert f"## [{NEW}] — {today}" in changelog
    assert "## [Unreleased]" not in changelog
    #: prior release notes are untouched
    assert f"## [{OLD}] — 2026-07-21" in changelog


def test_bump_rejects_a_malformed_target(tmp_path, monkeypatch):
    make_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="must be X.Y.Z"):
        bump("v0.1.3")


def test_bump_rejects_the_current_version(tmp_path, monkeypatch):
    make_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match=f"already at {OLD}"):
        bump(OLD)


def test_bump_changes_nothing_when_a_pin_is_stale(tmp_path, monkeypatch):
    make_tree(tmp_path)
    (tmp_path / "docker-bake.hcl").write_text("no pin here\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="docker-bake.hcl"):
        bump(NEW)
    #: the failure happens before any write: every other file keeps the old pin
    for token in version_pins(OLD)["README.md"]:
        assert token in (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")


def test_bump_requires_release_notes(tmp_path, monkeypatch):
    make_tree(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n## [0.1.2] — 2026-07-21\n\n- Prior.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="write release notes first"):
        bump(NEW)


def test_stamped_changelog_requires_the_unreleased_section():
    with pytest.raises(SystemExit, match="has no"):
        stamped_changelog("# Changelog\n\n## [0.1.0] — 2026-01-01\n", NEW, "2026-07-22")
