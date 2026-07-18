"""Mechanical release guards (HARNESS §6).

Version, MIT license, public metadata, and GitHub Actions stay aligned with
the local gate. These tests run inside ``make check``.
"""

import hashlib
import json
import re
import tomllib
from pathlib import Path

import yaml

from cdpx import __version__

PYPROJECT = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
REPOSITORY_URL = "https://github.com/inem0o/cdpx"


def test_action_journal_layer_does_not_depend_on_browser_primitives():
    journal_source = Path("src/cdpx/journal.py").read_text(encoding="utf-8")
    action_source = Path("src/cdpx/action_model.py").read_text(encoding="utf-8")

    assert "cdpx.primitives" not in journal_source
    assert "cdpx.primitives" not in action_source


def test_version_has_single_source():
    """The version has a single source of truth (cdpx.__version__),
    dynamically delegated by pyproject, and respects the x.y.z format."""
    # The version lives in cdpx.__version__; pyproject declares it dynamic.
    #: pyproject delegates the version to the package attribute: no static
    #: value to desynchronize when tagging a release
    assert "version" not in PYPROJECT["project"], "static version reintroduced in pyproject"
    assert "version" in PYPROJECT["project"]["dynamic"]
    assert PYPROJECT["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "cdpx.__version__"
    #: exactly two dots = semver x.y.z, the format expected by v* tags and
    #: the CHANGELOG
    assert __version__.count(".") == 2  # x.y.z


def test_license_is_declared_and_present():
    """The MIT license is declared as an SPDX expression, the LICENSE file
    tells the same story, and no private or deprecated classifier blocks
    publication."""
    project = PYPROJECT["project"]
    #: the SPDX declaration and the LICENSE file text must carry the same
    #: license, with the right copyright holder
    assert project["license"] == "MIT"
    license_text = Path("LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n")
    assert "Copyright (c) 2026 inem0o" in license_text
    #: neither an anti-upload guard nor a License:: classifier (deprecated
    #: by PEP 639): the package is publishable as-is and the license lives
    #: in license-files
    assert "Private :: Do Not Upload" not in project["classifiers"]
    assert not any(item.startswith("License ::") for item in project["classifiers"])
    assert project["license-files"] == ["LICENSE"]


def test_markdown_dependency_and_vendored_mermaid_notice_are_pinned():
    """The markdown dependency is bounded on major version and the
    vendored mermaid bundle is hash-pinned with license and third-party
    notice: no anonymous third-party code travels inside the package."""
    #: the <5 bound prevents a markdown-it-py major bump from silently
    #: changing the rendering of proof reports
    assert "markdown-it-py>=4.2,<5" in PYPROJECT["project"]["dependencies"]
    bundle = Path("src/cdpx/proofing/vendor/mermaid-11.16.0.min.js")
    license_path = Path("src/cdpx/proofing/vendor/LICENSE.mermaid")
    notice = Path("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    #: the bundle and its license are shipped, and the hash freezes the
    #: artifact: any substitution breaks the build instead of going unnoticed
    assert bundle.is_file() and license_path.is_file()
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == (
        "74d7c46dabca328c2294733910a8aa1ed0c37451776e8d5295da38a2b758fb9b"
    )
    #: the third-party notice cites the exact version and the MIT license
    #: accompanies the vendoring, a condition for clean redistribution
    assert "mermaid@11.16.0" in notice and "LICENSE.mermaid" in notice
    assert "MIT License" in license_path.read_text(encoding="utf-8")


def test_vendored_xterm_bundle_and_notice_are_pinned():
    """The vendored xterm player (js and css) is hash-pinned and
    accompanied by its license and third-party notice: same requirement as
    for mermaid."""
    bundle = Path("src/cdpx/proofing/vendor/xterm-5.5.0.min.js")
    stylesheet = Path("src/cdpx/proofing/vendor/xterm-5.5.0.min.css")
    license_path = Path("src/cdpx/proofing/vendor/LICENSE.xterm")
    notice = Path("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert bundle.is_file() and stylesheet.is_file() and license_path.is_file()
    #: the player bundle is pinned: any substitution breaks the build
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == (
        "4196e242ef1cf4c2adead8d97f4a772a69576076f70b095e004b4abbb049e7bf"
    )
    assert hashlib.sha256(stylesheet.read_bytes()).hexdigest() == (
        "f7f724aea2bb620a6482bfb8e4bdecfae1152b0c7facef55fbda61f3b6cfedb2"
    )
    #: the MIT license and third-party notice accompany the vendoring
    assert "@xterm/xterm@5.5.0" in notice and "LICENSE.xterm" in notice
    assert "Copyright" in license_path.read_text(encoding="utf-8")


def test_public_project_metadata_points_to_github():
    """The package's public metadata (author, repository, issues, changelog)
    all point to the canonical GitHub repository."""
    project = PYPROJECT["project"]
    #: every URL published on PyPI derives from the same repository: no
    #: dead link nor leftover from a former hosting
    assert project["authors"] == [{"name": "inem0o"}]
    assert project["urls"]["Repository"] == REPOSITORY_URL
    assert project["urls"]["Issues"] == f"{REPOSITORY_URL}/issues"
    assert project["urls"]["Changelog"].startswith(REPOSITORY_URL)


def test_changelog_covers_current_version():
    """The CHANGELOG contains a section for the package's current version:
    impossible to tag a release without release notes."""
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    #: the section is searched for the version imported from the package,
    #: not a hand-copied value: the version/notes link is mechanical
    assert f"## [{__version__}]" in changelog, f"CHANGELOG has no entry for {__version__}"


def test_python_floor_matches_ruff_target():
    """The declared Python floor and the ruff/mypy targets name the same
    version: tooling cannot validate syntax the package doesn't promise to
    support."""
    floor = PYPROJECT["project"]["requires-python"].removeprefix(">=").strip()
    ruff_target = PYPROJECT["tool"]["ruff"]["target-version"]
    #: ruff and mypy are derived from the same requires-python floor:
    #: raising one without the others would be a silent misalignment
    assert ruff_target == "py" + floor.replace(".", ""), (
        f"requires-python {floor} and ruff target-version {ruff_target} misaligned"
    )
    mypy_target = PYPROJECT["tool"]["mypy"]["python_version"]
    assert mypy_target == floor


def test_dev_extra_pins_the_toolchain():
    """The dev extra bundles the gate's entire toolchain: installing
    `.[dev]` is enough to replay make check-local with no implicit
    dependency on the environment."""
    dev = PYPROJECT["project"]["optional-dependencies"]["dev"]
    for tool in ("pytest", "pytest-cov", "ruff", "build", "twine", "mypy"):
        #: every tool of the gate must be installable via the extra,
        #: otherwise the check would depend on a particular workstation
        assert any(dep.startswith(tool) for dep in dev), f"missing dev tool: {tool}"


def test_release_image_contains_metadata_and_full_dev_toolchain():
    """The release Docker image carries the legal files and the full dev
    toolchain, and every base image is pinned by digest."""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    #: the legal files travel in the image and the install includes the
    #: dev extra: the gate is replayable inside the container
    assert "COPY LICENSE THIRD_PARTY_NOTICES.md CHANGELOG.md" in dockerfile
    assert 'pip install -e ".[dev]"' in dockerfile
    #: no residue of the former GitLab CI in the build context
    assert ".gitlab-ci.yml" not in dockerfile
    for path in (Path("Dockerfile"), Path("tests/symfony-app/Dockerfile")):
        from_line = path.read_text(encoding="utf-8").splitlines()[0]
        #: a FROM on a floating tag makes the build non-reproducible; only
        #: a sha256 digest actually freezes the base image
        assert re.fullmatch(r"FROM [^\s]+@sha256:[0-9a-f]{64}", from_line), (
            f"Docker image not pinned by digest: {path}"
        )


def test_release_portal_and_ci_require_all_runtime_proofs():
    """The make check gate aggregates every runtime proof, make release
    chains check + proof + verified distribution, and the GitHub CI
    replays these gates while publishing the shareable proofs."""
    makefile = Path("Makefile").read_text(encoding="utf-8")
    check_prerequisites = makefile.split("check:", 1)[1].split("\n", 1)[0]
    #: removing a runtime gate from check's prerequisites would silently
    #: weaken the Definition of Done: the list is frozen here
    for portal in ("check-local", "docker-check", "docker-e2e", "docker-symfony-e2e"):
        assert portal in check_prerequisites
    assert "docker run --rm cdpx-ci make check-local" in makefile

    #: release cannot short-circuit the check, the proof, nor the
    #: distribution build
    assert "release:" in makefile
    for portal in ("check", "proof", "dist"):
        assert portal in makefile.split("release:", 1)[1].split("\n", 1)[0]

    #: the distribution is smoke-tested and verified before any publication
    assert "smoke-dist" in makefile.split("dist:", 1)[1]
    assert "scripts/verify_dist.py" in makefile.split("dist:", 1)[1]

    #: the GitLab -> GitHub migration is complete: no more GitLab CI file
    assert not Path(".gitlab-ci.yml").exists()
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    #: the CI replays the make gates (release included), publishes the
    #: summary and the shareable proofs, with no redundant logs nor path
    #: filtering that would let an unverified commit through
    assert "make check-local" in ci
    assert "make cov" in ci
    assert "make release" in ci
    assert "include-hidden-files: true" in ci
    assert "name: PR Gate / Required" in ci
    assert "if: ${{ always() }}" in ci
    assert "GITHUB_STEP_SUMMARY" in ci
    assert "scripts/github_summary.py" in ci
    assert ".ci-artifacts/make-release.log" not in ci
    assert "tee .ci-artifacts" not in ci
    assert 'CDPX_PROOF_RETENTION_DAYS: "14"' in ci
    assert "path: .proof/shareable/" in ci
    assert "paths:" not in ci and "paths-ignore:" not in ci

    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    compose = Path("docker-compose.symfony-e2e.yml").read_text(encoding="utf-8")
    #: the release keeps its proofs longer than the CI, and the Symfony
    #: loop receives the same retention policy
    assert 'CDPX_PROOF_RETENTION_DAYS: "30"' in release
    assert "path: .proof/shareable/" in release
    assert "CDPX_PROOF_RETENTION_DAYS" in compose


def test_github_workflows_are_parseable_and_actions_are_sha_pinned():
    """GitHub workflows are valid YAML and every third-party action is
    pinned by full SHA: nothing mutable in the CI chain."""
    workflows = sorted(Path(".github/workflows").glob("*.yml"))
    #: the workflow inventory is exhaustive: a workflow added must go
    #: through this guard or be explicitly listed
    assert {path.name for path in workflows} == {"ci.yml", "pages.yml", "release.yml"}
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        parsed = yaml.load(text, Loader=yaml.BaseLoader)
        #: BaseLoader proves the file is parseable YAML with the minimal
        #: keys of a workflow, with no interpretation of values
        assert isinstance(parsed, dict) and "on" in parsed and "jobs" in parsed
        for action in re.findall(r"^\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE):
            #: a tag or branch can be rewritten after the fact; only a
            #: 40-character SHA actually pins the code executed by the CI
            assert re.fullmatch(r"[^/@]+/[^/@]+@[0-9a-f]{40}", action), (
                f"action not SHA-pinned in {path}: {action}"
            )


def test_ci_and_release_workflows_keep_permissions_narrow():
    """The CI stays read-only (neither OIDC nor pull_request_target) and
    the release grants each job exactly the permission it needs."""
    ci_text = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    #: the CI can write nothing, issues no OIDC token, and does not expose
    #: secrets to fork code
    assert re.search(r"^permissions:\n  contents: read$", ci_text, re.MULTILINE)
    assert "id-token: write" not in ci_text
    assert "pull_request_target" not in ci_text

    release_text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    #: the release only starts from a v* tag, publishes via OIDC in a
    #: protected environment, and verifies that the tag matches the
    #: version and does descend from master
    assert "tags:" in release_text and '- "v*"' in release_text
    assert "environment:" in release_text and "name: pypi" in release_text
    assert "id-token: write" in release_text
    assert "pypa/gh-action-pypi-publish@" in release_text
    assert "gh release create" in release_text
    assert '"v${PACKAGE_VERSION}" = "${GITHUB_REF_NAME}"' in release_text
    assert "GH_REPO: ${{ github.repository }}" in release_text
    assert "(cd dist && sha256sum *) > SHA256SUMS" in release_text
    assert 'git merge-base --is-ancestor "${GITHUB_SHA}" origin/master' in release_text

    release = yaml.load(release_text, Loader=yaml.BaseLoader)
    jobs = release["jobs"]
    #: every job declares exactly its permission (least privilege): no job
    #: inherits a global right from the workflow
    assert jobs["publish-pypi"]["permissions"] == {"id-token": "write"}
    assert jobs["github-release"]["permissions"] == {"contents": "write"}
    assert jobs["build-and-verify"]["permissions"] == {"contents": "read"}


def test_public_community_files_and_generated_artifact_policy():
    """Community files and GitHub configuration exist, and generated
    artifacts stay out of the repository as well as the Docker context."""
    #: community health files condition the reception of contributions on
    #: a public repository
    for name in ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "SUPPORT.md"):
        assert Path(name).is_file(), f"missing community file: {name}"
    #: issue/PR templates and dependabot are part of the GitHub-side
    #: tooled contribution contract
    for name in (
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/dependabot.yml",
    ):
        assert Path(name).is_file(), f"missing GitHub configuration: {name}"

    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    #: generated outputs (proofs, CI artifacts) and editorial content leak
    #: into neither git nor the Docker image
    assert ".proof/" in gitignore
    assert ".ci-artifacts/" in gitignore
    assert ".proof" in dockerignore
    assert ".ci-artifacts" in dockerignore
    assert "article/" in dockerignore and "presentation/" in dockerignore
    assert Path("MANIFEST.in").is_file()


def test_github_templates_enforce_the_project_contract():
    """The GitHub templates (PR, bug, feature) mechanically recall the
    project contract: required gate, harness invariants, and Definition of
    Done — a contributor cannot ignore them by accident."""
    pr_template = Path(".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    #: the PR template names the blocking check, refuses the checkbox as
    #: proof, and covers each harness invariant
    assert "PR Gate / Required" in pr_template
    assert "declarative checkbox never replaces" in pr_template
    for requirement in ("CLI contract", "CDP protocol", "Fixture", "Security", "make release"):
        assert requirement.lower() in pr_template.lower()

    bug = yaml.safe_load(Path(".github/ISSUE_TEMPLATE/bug_report.yml").read_text())
    #: the bug report forces the fields necessary for a reproduction that
    #: complies with the CLI contract (command, outputs, version, environment)
    assert bug["labels"] == ["bug"]
    bug_ids = {item.get("id") for item in bug["body"]}
    assert {"command", "stdout-stderr", "version", "environment"} <= bug_ids

    feature = yaml.safe_load(Path(".github/ISSUE_TEMPLATE/feature_request.yml").read_text())
    #: the feature request embeds the harness's Definition of Done as
    #: checkboxes: primitive, CLI, protocol, fixture, e2e
    assert feature["labels"] == ["enhancement"]
    dod = next(item for item in feature["body"] if item.get("id") == "definition-of-done")
    labels = " ".join(option["label"] for option in dod["attributes"]["options"])
    for requirement in ("primitive", "subcommand", "JSON", "protocol", "fixture", "E2E"):
        assert requirement.lower() in labels.lower()

    contributing = Path("CONTRIBUTING.md").read_text(encoding="utf-8")
    #: CONTRIBUTING routes to the GitHub doc and names the same required
    #: check as the PR template: a single vocabulary for the gate
    assert "docs/GITHUB.md" in contributing and "PR Gate / Required" in contributing


def test_derived_symfony_fixtures_retain_upstream_notice():
    """Material derived from Symfony keeps its upstream license notice, and
    the reference app locks in only MIT/BSD-3-Clause dependencies."""
    notice = Path("tests/fixtures/profiler/LICENSE.SYMFONY").read_text(encoding="utf-8")
    fixture_readme = Path("tests/fixtures/profiler/README.md").read_text(encoding="utf-8")
    #: the derived fixture keeps the upstream copyright and its README
    #: points to the notice: provenance stays traceable
    assert "Copyright (c) 2004-present Fabien Potencier" in notice
    assert "Permission is hereby granted" in notice
    assert "LICENSE.SYMFONY" in fixture_readme

    composer = json.loads(Path("tests/symfony-app/composer.lock").read_text(encoding="utf-8"))
    licenses = {
        license_name
        for package in composer["packages"]
        for license_name in package.get("license", [])
    }
    #: the composer lock cannot introduce any more restrictive license
    #: into the repository via the reference app's dependencies
    assert licenses <= {"MIT", "BSD-3-Clause"}
