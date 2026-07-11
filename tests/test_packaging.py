"""Garde-fous release mécaniques (HARNESS §6).

Version, licence MIT, métadonnées publiques et GitHub Actions restent alignés
avec le portail local. Ces tests tournent dans ``make check``.
"""

import json
import re
import tomllib
from pathlib import Path

import yaml

from cdpx import __version__

PYPROJECT = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
REPOSITORY_URL = "https://github.com/inem0o/cdpx"


def test_version_has_single_source():
    # La version vit dans cdpx.__version__; pyproject la déclare dynamic.
    assert "version" not in PYPROJECT["project"], "version statique réintroduite dans pyproject"
    assert "version" in PYPROJECT["project"]["dynamic"]
    assert PYPROJECT["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "cdpx.__version__"
    assert __version__.count(".") == 2  # x.y.z


def test_license_is_declared_and_present():
    project = PYPROJECT["project"]
    assert project["license"] == "MIT"
    license_text = Path("LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n")
    assert "Copyright (c) 2026 inem0o" in license_text
    assert "Private :: Do Not Upload" not in project["classifiers"]
    assert not any(item.startswith("License ::") for item in project["classifiers"])
    assert project["license-files"] == ["LICENSE"]


def test_public_project_metadata_points_to_github():
    project = PYPROJECT["project"]
    assert project["authors"] == [{"name": "inem0o"}]
    assert project["urls"]["Repository"] == REPOSITORY_URL
    assert project["urls"]["Issues"] == f"{REPOSITORY_URL}/issues"
    assert project["urls"]["Changelog"].startswith(REPOSITORY_URL)


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


def test_release_image_contains_metadata_and_full_dev_toolchain():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "COPY LICENSE CHANGELOG.md" in dockerfile
    assert 'pip install -e ".[dev]"' in dockerfile
    assert ".gitlab-ci.yml" not in dockerfile
    for path in (Path("Dockerfile"), Path("tests/symfony-app/Dockerfile")):
        from_line = path.read_text(encoding="utf-8").splitlines()[0]
        assert re.fullmatch(r"FROM [^\s]+@sha256:[0-9a-f]{64}", from_line), (
            f"image Docker non épinglée par digest: {path}"
        )


def test_release_portal_and_ci_require_all_runtime_proofs():
    makefile = Path("Makefile").read_text(encoding="utf-8")
    check_prerequisites = makefile.split("check:", 1)[1].split("\n", 1)[0]
    for portal in ("check-local", "docker-check", "docker-e2e", "docker-symfony-e2e"):
        assert portal in check_prerequisites
    assert "docker run --rm cdpx-ci make check-local" in makefile

    assert "release:" in makefile
    for portal in ("check", "proof", "dist"):
        assert portal in makefile.split("release:", 1)[1].split("\n", 1)[0]

    assert "smoke-dist" in makefile.split("dist:", 1)[1]
    assert "scripts/verify_dist.py" in makefile.split("dist:", 1)[1]

    assert not Path(".gitlab-ci.yml").exists()
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "make check-local" in ci
    assert "make cov" in ci
    assert "make release" in ci
    assert "include-hidden-files: true" in ci


def test_github_workflows_are_parseable_and_actions_are_sha_pinned():
    workflows = sorted(Path(".github/workflows").glob("*.yml"))
    assert {path.name for path in workflows} == {"ci.yml", "release.yml"}
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        parsed = yaml.load(text, Loader=yaml.BaseLoader)
        assert isinstance(parsed, dict) and "on" in parsed and "jobs" in parsed
        for action in re.findall(r"^\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE):
            assert re.fullmatch(r"[^/@]+/[^/@]+@[0-9a-f]{40}", action), (
                f"action non épinglée par SHA dans {path}: {action}"
            )


def test_ci_and_release_workflows_keep_permissions_narrow():
    ci_text = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^permissions:\n  contents: read$", ci_text, re.MULTILINE)
    assert "id-token: write" not in ci_text
    assert "pull_request_target" not in ci_text

    release_text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "tags:" in release_text and '- "v*"' in release_text
    assert "environment:" in release_text and "name: pypi" in release_text
    assert "id-token: write" in release_text
    assert "pypa/gh-action-pypi-publish@" in release_text
    assert "gh release create" in release_text
    assert '"v${PACKAGE_VERSION}" = "${GITHUB_REF_NAME}"' in release_text
    assert "GH_REPO: ${{ github.repository }}" in release_text
    assert "(cd dist && sha256sum *) > SHA256SUMS" in release_text

    release = yaml.load(release_text, Loader=yaml.BaseLoader)
    jobs = release["jobs"]
    assert jobs["publish-pypi"]["permissions"] == {"id-token": "write"}
    assert jobs["github-release"]["permissions"] == {"contents": "write"}
    assert jobs["build-and-verify"]["permissions"] == {"contents": "read"}


def test_public_community_files_and_generated_artifact_policy():
    for name in ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "SUPPORT.md"):
        assert Path(name).is_file(), f"fichier communautaire manquant: {name}"
    for name in (
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/dependabot.yml",
    ):
        assert Path(name).is_file(), f"configuration GitHub manquante: {name}"

    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    assert ".proof/" in gitignore
    assert ".proof" in dockerignore
    assert Path("MANIFEST.in").is_file()


def test_derived_symfony_fixtures_retain_upstream_notice():
    notice = Path("tests/fixtures/profiler/LICENSE.SYMFONY").read_text(encoding="utf-8")
    fixture_readme = Path("tests/fixtures/profiler/README.md").read_text(encoding="utf-8")
    assert "Copyright (c) 2004-present Fabien Potencier" in notice
    assert "Permission is hereby granted" in notice
    assert "LICENSE.SYMFONY" in fixture_readme

    composer = json.loads(Path("tests/symfony-app/composer.lock").read_text(encoding="utf-8"))
    licenses = {
        license_name
        for package in composer["packages"]
        for license_name in package.get("license", [])
    }
    assert licenses <= {"MIT", "BSD-3-Clause"}
