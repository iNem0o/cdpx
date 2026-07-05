+++
id = "harness-proof-cockpit"
title = "Harness and proof cockpit"
status = "active"
summary = "Run deterministic quality portals and publish a central feature-centric validation cockpit."
entrypoints = ["make help", "make setup", "make check", "make lint", "make fmt", "make test", "make test-e2e", "make fixtures", "make mock", "make docker-build", "make docker-check", "make docker-e2e", "make proof", "make clean", "make dist", "python -m cdpx.proof"]
path_globs = ["Makefile", "pyproject.toml", "Dockerfile", ".gitlab-ci.yml", "src/cdpx/__init__.py", "src/cdpx/cli.py", "src/cdpx/output.py", "src/cdpx/primitives/__init__.py", "src/cdpx/proof.py", "src/cdpx/proofing/*.py", "src/cdpx/testing/*.py", "tests/conftest.py", "tests/e2e/test_e2e_chrome.py", "tests/fixtures/pixel.png", "tests/test_cli.py", "tests/test_evidence.py", "tests/test_features.py", "tests/test_fixture_server.py", "tests/test_primitives.py", "tests/test_proof.py", "README.md", "HARNESS.md", "CLAUDE.md", "docs/*.md", "docs/features/*.md", "docs/milestones/*.md"]
test_globs = ["tests/test_proof.py::*", "tests/test_features.py::*", "tests/test_evidence.py::*", "tests/test_fixture_server.py::*", "tests/test_cli.py::test_pretty*", "tests/test_cli.py::test_agent_output*", "tests/test_cli.py::test_discovery_error*", "tests/test_cli.py::test_usage_error*", "tests/test_cli.py::test_origin_guard*", "tests/test_cli.py::test_cdpx_version"]
docs = ["README.md", "HARNESS.md", "CLAUDE.md", "docs/VALIDATION.md", "docs/ROADMAP.md", "docs/TODO.md"]
expected_proofs = ["junit"]

[[journeys]]
id = "run-quality-gate"
title = "Run lint, format and deterministic tests"
entrypoint = "make check"

[[journeys]]
id = "publish-proof"
title = "Generate the human and machine validation report"
entrypoint = "make proof"

[[scenarios]]
id = "run-local-quality-gate"
journey = "run-quality-gate"
title = "Run local quality gates"
ui_text = "The developer can run the deterministic lint, format and unit-test portal."
report_text = "This scenario proves that the project keeps a local quality gate before producing heavier browser evidence."
given = "The repository dependencies are installed locally."
when = "The harness runs lint, format checks and deterministic tests."
then = "Failures are surfaced as command evidence and JUnit summaries."
tests = ["tests/test_fixture_server.py::*", "tests/test_cli.py::test_pretty*", "tests/test_cli.py::test_agent_output*", "tests/test_cli.py::test_discovery_error*", "tests/test_cli.py::test_usage_error*", "tests/test_cli.py::test_origin_guard*", "tests/test_cli.py::test_cdpx_version"]
expected_proofs = ["junit"]

[[scenarios]]
id = "publish-feature-proof"
journey = "publish-proof"
title = "Publish a feature-centric proof cockpit"
ui_text = "The generated report lets a human navigate from product feature to journey, scenario, test and evidence."
report_text = "This scenario proves that the report can be reviewed as a product-oriented cockpit instead of a flat CI artifact list."
given = "Feature docs, pytest evidence, JUnit XML and command logs exist for the run."
when = "python -m cdpx.proof builds the validation summary and HTML report."
then = "The report exposes feature dossiers, scenario explanations, tests, screenshots and gaps from one artifact."
tests = ["tests/test_proof.py::*", "tests/test_features.py::*", "tests/test_evidence.py::*"]
expected_proofs = ["junit"]
+++

## Intent

Make the project harness observable, reproducible and reviewable through one
central cockpit.

## User journeys

- Run local quality gates.
- Generate `.proof/proof-report.html` and `.proof/validation-summary.json`.
- Inspect feature, scenario, test and proof coverage from one page.

## Validation

Unit tests validate parsing, summary compatibility and proof failure rules.

## Evidence

Expected evidence is JUnit plus generated proof artifacts.

## Known gaps

Docker e2e portals remain explicit heavy checks and are not always run by
default.
