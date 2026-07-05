"""Garde-fous release mécaniques (HARNESS §6): version unique, licence et
changelog présents, versions Python alignées. Tournent dans `make check`."""

import tomllib
from pathlib import Path

from cdpx import __version__

PYPROJECT = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


def test_version_has_single_source():
    # La version vit dans cdpx.__version__; pyproject la déclare dynamic.
    assert "version" not in PYPROJECT["project"], "version statique réintroduite dans pyproject"
    assert "version" in PYPROJECT["project"]["dynamic"]
    assert PYPROJECT["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "cdpx.__version__"
    assert __version__.count(".") == 2  # x.y.z


def test_license_is_declared_and_present():
    assert PYPROJECT["project"]["license"] == "LicenseRef-Proprietary"
    license_text = Path("LICENSE").read_text(encoding="utf-8")
    assert "inem0o" in license_text and "Tous droits réservés" in license_text
    assert "Private :: Do Not Upload" in PYPROJECT["project"]["classifiers"]


def test_changelog_covers_current_version():
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in changelog, f"CHANGELOG sans entrée pour {__version__}"


def test_python_floor_matches_ruff_target():
    floor = PYPROJECT["project"]["requires-python"].removeprefix(">=").strip()
    ruff_target = PYPROJECT["tool"]["ruff"]["target-version"]
    assert ruff_target == "py" + floor.replace(".", ""), (
        f"requires-python {floor} et ruff target-version {ruff_target} désalignés"
    )
    mypy_target = PYPROJECT["tool"]["mypy"]["python_version"]
    assert mypy_target == floor


def test_dev_extra_pins_the_toolchain():
    dev = PYPROJECT["project"]["optional-dependencies"]["dev"]
    for tool in ("pytest", "pytest-cov", "ruff", "build", "twine", "mypy"):
        assert any(dep.startswith(tool) for dep in dev), f"outil dev manquant: {tool}"
