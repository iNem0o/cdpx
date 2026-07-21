+++
id = "harness-proof-cockpit"
title = "Harness and proof cockpit"
status = "validated"
summary = "Run deterministic quality gates and publish a central, feature-oriented validation cockpit."
entrypoints = ["./dev help", "./dev setup", "./dev check-local", "./dev check", "./dev fmt", "./dev test-e2e", "./dev fixtures", "./dev mock", "./dev site-record", "./dev image", "./dev proof", "./dev release", "./dev clean", "python -m cdpx.proof"]
path_globs = ["dev", "cdpx", "Makefile", "skills/*", "pyproject.toml", "uv.lock", "MANIFEST.in", "scripts/*.py", "Dockerfile", "docker-bake.hcl", "packaging/*", "schemas/*.json", ".gitignore", ".dockerignore", ".github/workflows/*.yml", ".github/ISSUE_TEMPLATE/*.yml", ".github/*.md", ".github/dependabot.yml", "src/cdpx/__init__.py", "src/cdpx/cli.py", "src/cdpx/output.py", "src/cdpx/runtime.py", "src/cdpx/runtime_config.py", "src/cdpx/primitives/__init__.py", "src/cdpx/proof.py", "src/cdpx/proofing/*.py", "src/cdpx/proofing/vendor/*", "src/cdpx/proofing/cockpit/*", "src/cdpx/testing/*.py", "tests/conftest.py", "tests/e2e/test_e2e_chrome.py", "tests/fixtures/pixel.png", "tests/test_cli.py", "tests/test_coverage_gate.py", "tests/test_documentation.py", "tests/test_evidence.py", "tests/test_intent.py", "tests/test_cast.py", "tests/test_e2e_helpers.py", "tests/test_features.py", "tests/test_fixture_server.py", "tests/test_github_summary.py", "tests/test_primitives.py", "tests/test_proof.py", "tests/test_runtime_config.py", "tests/test_tooling_contract.py", "tests/test_markdown.py", "tests/test_docs.py", "tests/test_packaging.py", "tests/test_public_surfaces.py", "README.md", "THIRD_PARTY_NOTICES.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "SUPPORT.md", "HARNESS.md", "CLAUDE.md", "docs/*.md", "docs/*.toml", "docs/*.yaml", "docs/features/*.md", "src/cdpx/cli_context.py", "src/cdpx/commands/*.py", "src/cdpx/option_types.py"]
test_globs = ["tests/test_proof.py::*", "tests/test_features.py::*", "tests/test_evidence.py::*", "tests/test_intent.py::*", "tests/test_cast.py::*", "tests/test_coverage_gate.py::*", "tests/test_runtime_config.py::*", "tests/test_tooling_contract.py::*", "tests/test_e2e_helpers.py::*", "tests/test_github_summary.py::*", "tests/test_markdown.py::*", "tests/test_documentation.py::*", "tests/test_docs.py::*", "tests/test_packaging.py::*", "tests/test_public_surfaces.py::*", "tests/test_fixture_server.py::*", "tests/test_cli.py::test_pretty*", "tests/test_cli.py::test_agent_output*", "tests/test_cli.py::test_discovery_error*", "tests/test_cli.py::test_usage_error*", "tests/test_cli.py::test_origin_guard*", "tests/test_cli.py::test_cli_dispatch*", "tests/test_cli.py::test_cdpx_version", "tests/test_cli.py::test_conditional_cli_arguments*", "tests/test_cli.py::test_cookie_mutations_and_vitals*", "tests/e2e/test_e2e_chrome.py::test_cli_stdout_stderr*", "tests/e2e/test_e2e_chrome.py::test_proof_cockpit_renders_offline_docs_and_mermaid", "tests/e2e/test_e2e_chrome.py::test_cockpit_*", "tests/e2e/test_e2e_chrome.py::test_modal_*", "tests/test_cli.py::test_command_options_*", "tests/test_cli.py::test_prepare_builds_immutable_typed_invocation"]
docs = ["README.md", "HARNESS.md", "docs/AGENT-GUIDE.md", "docs/CONTEXT.md", "docs/VALIDATION.md", "docs/RELEASING.md", "docs/INSTALLATION.md", "docs/CONFIGURATION.md", "docs/INTEGRATION.md", "docs/DEVELOPMENT.md", "docs/RELEASE-ARCHITECTURE.md", "docs/TROUBLESHOOTING.md"]
expected_proofs = ["junit"]

[[journeys]]
id = "run-quality-gate"
title = "Run lint, format and deterministic tests"
entrypoint = "./dev check"

[[journeys]]
id = "publish-proof"
title = "Generate the human- and machine-readable validation report"
entrypoint = "./dev proof"

[[journeys]]
id = "ship-runtime"
title = "Build and promote the same validated OCI runtime"
entrypoint = "./dev release"

[[scenarios]]
id = "run-local-quality-gate"
journey = "run-quality-gate"
title = "Run the local quality gates"
ui_text = "The developer can run the deterministic lint + format + unit test gate."
report_text = "This scenario proves that the project maintains a local quality gate before producing heavier browser proofs."
given = "The repository dependencies are installed locally."
when = "The harness runs lint, format check and deterministic tests, including the CLI dispatch safety net (harness contract test)."
then = "Failures surface as command proofs and JUnit summaries."
tests = ["tests/test_fixture_server.py::*", "tests/test_cli.py::test_pretty*", "tests/test_cli.py::test_agent_output*", "tests/test_cli.py::test_discovery_error*", "tests/test_cli.py::test_usage_error*", "tests/test_cli.py::test_origin_guard*", "tests/test_cli.py::test_cli_dispatch*", "tests/test_cli.py::test_cdpx_version", "tests/test_cli.py::test_command_options_*", "tests/test_cli.py::test_prepare_builds_immutable_typed_invocation"]
expected_proofs = ["junit"]

[[scenarios]]
id = "enforce-independent-coverage-gates"
journey = "run-quality-gate"
title = "Enforce line and branch coverage independently"
ui_text = "The short gate rejects a regression in either line or branch coverage."
report_text = "This scenario proves that aggregate line coverage cannot hide insufficient branch coverage, and conversely."
given = "Coverage.py has emitted a machine-readable coverage report."
when = "The harness evaluates line and branch percentages against their separately configured thresholds."
then = "Both percentages must pass and both measured values remain visible in the gate output."
tests = ["tests/test_coverage_gate.py::*"]
expected_proofs = ["junit"]

[[scenarios]]
id = "compile-runtime-configuration"
journey = "ship-runtime"
title = "Validate and compile the runtime configuration"
ui_text = "Integrators receive strict defaults and fail-closed validation for every runtime option."
report_text = "This scenario proves that cdpx.yaml is compiled into a bounded runtime plan and forwards only explicitly declared environment variables."
given = "A working tree contains either no configuration or an integrator-authored cdpx.yaml."
when = "The launcher compiles the runtime configuration before creating or replacing its persistent container."
then = "Defaults are deterministic, unknown or unsafe values fail closed, and undeclared host environment is absent."
tests = ["tests/test_runtime_config.py::*"]
expected_proofs = ["junit"]

[[scenarios]]
id = "standardize-oci-tooling-and-release"
journey = "ship-runtime"
title = "Keep tooling, documentation and release promotion standardized"
ui_text = "Development, CI and release use one pinned image graph and every new surface maps to user and integrator documentation."
report_text = "This scenario proves the multi-stage Docker contract, POSIX host portals, strict configuration schema, digest-only release promotion and documentation matrix."
given = "The repository contains the Docker graph, launchers, workflows, schema and canonical documentation matrix."
when = "The tooling contract suite statically validates those surfaces and shellchecks every portable script."
then = "CI cannot rebuild during promotion, public scripts stay portable, and undocumented features fail the unit gate."
tests = ["tests/test_tooling_contract.py::*"]
expected_proofs = ["junit"]

[[scenarios]]
id = "publish-feature-proof"
journey = "publish-proof"
title = "Publish a feature-oriented proof cockpit"
ui_text = "The generated report lets a human navigate from the product feature to the journey, the scenario, the test and the proof."
report_text = "This scenario proves that the report reads as a product-oriented cockpit, not as a flat list of CI artifacts."
given = "The feature sheets, the pytest proofs, the JUnit XML and the command logs exist for the run."
when = "python -m cdpx.proof builds the validation summary and the HTML report, rendering the Markdown docs of the feature sheets."
then = "The local report links feature folders, scenarios, tests, private captures and gaps; the CI staging contains only the manifested and cleaned text files."
tests = ["tests/test_proof.py::*", "tests/test_features.py::*", "tests/test_evidence.py::*", "tests/test_intent.py::*", "tests/test_cast.py::*", "tests/test_e2e_helpers.py::*", "tests/test_github_summary.py::*", "tests/test_markdown.py::*", "tests/test_documentation.py::*", "tests/test_docs.py::*", "tests/test_packaging.py::*", "tests/e2e/test_e2e_chrome.py::test_proof_cockpit_renders_offline_docs_and_mermaid"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "navigate-cockpit-views"
journey = "publish-proof"
title = "Navigate the published cockpit views"
ui_text = "The report reader navigates from the home page to the Features, Gaps, Run, CLI, Validation and Project views in a real browser, offline."
report_text = "This scenario proves that the cockpit SPA renders each of its steering views in file:// alone: feature → journey → scenario → test card drill-down, the 'Read first' panel and gaps on a red run, the run timeline and casts, the full CLI surface, the validation matrix and project context, with an explicit fallback on an unknown route."
given = "A shareable proof report generated from a rich summary (feature with journey and pass/fail scenarios, commands, JUnit suites, casts, validation matrix)."
when = "The report is opened via file:// in a real Chrome and the reader follows the cockpit's routes and internal links."
then = "Each view renders its data without any network request and internal links connect features, scenarios, tests and proofs."
tests = ["tests/e2e/test_e2e_chrome.py::test_cockpit_features_view_drills_down_to_scenario", "tests/e2e/test_e2e_chrome.py::test_cockpit_read_first_and_gaps_surface_failures", "tests/e2e/test_e2e_chrome.py::test_cockpit_run_view_lists_commands_timeline_and_casts", "tests/e2e/test_e2e_chrome.py::test_cockpit_cli_and_validation_views", "tests/e2e/test_e2e_chrome.py::test_cockpit_project_view_and_unknown_route"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "inspect-artifact-viewers"
journey = "publish-proof"
title = "Inspect proofs in the artifact modal"
ui_text = "Each attached proof opens in a dedicated viewer within the modal, fully operable via keyboard."
report_text = "This scenario proves that every type in the closed taxonomy has a working viewer in the contextual modal: filterable console, network table, JSON tree, profiler, logs, highlighted excerpt, command transcript, zoomable screenshot, local video, download fallback and xterm cast player — with a trapped focus, bounded arrow-key navigation and Escape restoring focus."
given = "A shareable report where a scenario carries an inlined artifact of every type in the closed taxonomy."
when = "The reader opens the proofs from the scenario timeline and operates the modal via keyboard (arrows, Tab, Escape)."
then = "Each type renders in its dedicated viewer and keyboard navigation stays confined to the modal until it closes."
tests = ["tests/e2e/test_e2e_chrome.py::test_modal_renders_every_textual_viewer", "tests/e2e/test_e2e_chrome.py::test_modal_renders_media_and_cast_viewers", "tests/e2e/test_e2e_chrome.py::test_modal_keyboard_navigation_and_focus_trap"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "enforce-public-copy"
journey = "run-quality-gate"
title = "Keep public copy current and consistently English"
ui_text = "Public documentation, metadata and interfaces use one language and describe only the current product."
report_text = "This scenario proves that public copy is checked without a baseline: French prose, invalid document links and project-development narratives fail the unit gate."
given = "The repository defines the authored public surfaces and explicit third-party exclusions."
when = "The unit gate scans every selected surface and validates Markdown links."
then = "All public copy is English, current and connected to an existing document."
tests = ["tests/test_public_surfaces.py::*"]
expected_proofs = ["junit"]
+++

## Intent

Make the project's harness observable, reproducible and auditable through a
central cockpit. The Docker-only `./dev` portal owns the gates:
`./dev check` decides before any merge, and `./dev proof`
turns the collected proofs (JUnit, logs, private local captures, feature
sheets) into a feature-centric HTML report — the product's human
documentation, where every claim is linked to its proof.

## Usage

Global options and exit codes: see the CLI Contract section of the README.

### `./dev help`

Lists the supported development commands. This is the entry point for
discovering the harness.

### `./dev setup`

Builds the pinned development and production runtime images. Docker is the
only host dependency; Python and all project tools remain inside the images.

### `./dev check-local`

Runs Ruff, the formatting check, mypy, deterministic unit tests and separate
line/branch coverage thresholds inside the development image. This is the
short loop, not a release decision.

### `./dev check`

Runs the full proof-backed quality gate: static and unit checks, real
Chromium e2e, the real Dockerized Symfony suite, documentation contracts and
artifact-policy validation. Nothing merges unless it passes; every work
session ends with a green `./dev check`.

```bash
./dev check
```

### `./dev fmt`

Reformats the code (`ruff format`) and applies automatic fixes
(`ruff check --fix`). The corrective counterpart to `./dev check-local`.

### `./dev test-e2e`

Runs only the real-Chromium e2e suite inside the development image. Missing
Chromium is a failure.

### `./dev fixtures`

Starts the static reference site on port 8899, for manual inspection or a
hand-driven e2e.

### `./dev mock`

Runs a supervised session in the foreground with a mock backend to debug the
CLI without a browser. The command prints the `CDPX_SESSION`, `CDPX_RUN_ID`
and `CDPX_TARGET` exports, then waits. Business commands use these variables
exactly as with real Chrome; no raw endpoint is exposed.

```bash
./dev mock
```

In a second terminal, copy the printed exports then run for example
`cdpx goto http://demo.test/` and `cdpx tabs list`. `Ctrl-C` in the first
terminal stops the backend and removes the manifest, profile and private
artifacts.

### `./dev site-record`

(Re-)records the homepage tutorial casts (`site/assets/casts/*.cast`) via
`scripts/site_casts/generate.py`, then validates them (`check`). Requires a
real Chrome and Docker: the target starts the Symfony reference app via the
`docker-compose.site-casts.yml` overlay (dedicated compose project, loopback
:8025, volumes purged at teardown) to record the profiler demo, and each
scenario opens a disposable supervised session plus the static reference
site on :8899. The cdpx commands are actually executed and the cast is
written only if every expectation passes (genuine outputs and durations,
synthesized keystrokes). See `site/assets/casts/README.md`.

```bash
./dev site-record
```

### `./dev image`

Builds the production `runtime` target from the same pinned multi-stage
graph used by development and CI.

### `./dev clean`

Removes build, proof and cache artifacts (pytest, ruff, `.proof`, dist,
egg-info, `__pycache__`).

### `./dev proof`

Generates the human-readable HTML report from the proofs collected in
`.proof/` through the same containerized harness used by CI.

```bash
./dev proof
```

Docker/Compose and the real Symfony suite are required. An unavailability or
a skipped Symfony produces a red report and a non-zero exit.

### `./dev release`

Final aggregated gate: full `check`, a green proof cockpit, then verified
internal wheel build. The wheel is image-build evidence, not a public
distribution. CI publishes a candidate OCI image for each architecture;
release promotes the exact validated manifest digest without rebuilding.

```bash
./dev release
```

### `python -m cdpx.proof`

Builds the proof cockpit: reads the feature sheets from `docs/features/`
(strict TOML front matter + user-facing Markdown doc), the collected pytest
proofs, the JUnit XML, the command logs (`make-check-pytest.log`,
`e2e-chrome.log`, `symfony-e2e.log`), the CLI help and the git context, then
publishes two main artifacts into the private `.proof/` tree:

- `.proof/proof-report.html` — the feature-centric proof cockpit: the
  product's human documentation, navigable from feature to journey, to
  scenario, to test and to local proof (captures included), gaps included;
- `.proof/validation-summary.json` — the same content for machines
  (CI, agents), with inventory violations and warnings.

The **Docs** tab reads `docs/cockpit.toml`, reproduces the hierarchy of
curated files and renders their CommonMark. The eight feature sheets appear
there as functional specifications while staying attached to their journeys,
tests and proofs. `mermaid` fences are rendered offline by a pinned and
verified local bundle; no CDN is contacted when the report is opened.

The cockpit's presentation lives in `src/cdpx/proofing/cockpit/`
(`shell.html`, `cockpit.css` and the ordered parts `js/00-helpers.js` →
`js/50-router.js` concatenated into a single IIFE), loaded via
`importlib.resources` and shipped in the wheel. Every artifact type in the
closed taxonomy (`screenshot`, `video`, `console`, `network`, `json`,
`profiler`, `logs`, `log-excerpt`, `command`, `asciinema`, `file`) has a
dedicated viewer opened in a contextual modal (scenario wording, step, test,
relative timestamp, keyboard navigation). Textual content is inlined into
the report payload at build time (16 KB cap per artifact, 256 KB for
`.cast` files, global budgets of 2 MB for scenarios + 1 MB for casts,
excerpts honestly truncated beyond that) because the CSP forbids any network
loading.

Each test's intent flows up from the code itself: the docstring becomes the
method's intent, and the `#: <text>` comments placed above assertions become
an annotated, hierarchical trace correlated to the failure line (static
ast/tokenize extraction, no runtime impact). Secondary proofs — command
transcript (`attach_command_output`), targeted log excerpt
(`attach_log_excerpt`), terminal recording (`attach_cast`) — complement
screenshots and JSON.

`./dev proof` systematically records demonstration commands as `.cast`
(asciicast v2) via a native stdlib recorder (pty), with no `asciinema` or
`agg` dependency. This gate is blocking: a missing, degraded or oversized
cast fails the proof (`cast missing:`/`cast unavailable:` in
`proof_failures`). The catalog's casts are inlined and played in a real
vendored xterm.js terminal (MIT, SHA-256 verified like Mermaid), driven by
the in-house toolbar (playback, scrubber, speeds, raw fallback view).

Folders are forced to `0700` and files to `0600`. A `cdpx.artifacts/v1`
manifest classifies every file (`public`, `internal`, `secret`,
`opaque-restricted`), recording its SHA-256, redaction version, TTL and
upload permission. `./dev proof` then builds `.proof/shareable/` with only
the explicitly authorized `internal` text files. Captures, PDFs and binaries
stay opaque/restricted locally. A canary scan fails closed before
publication.

The PR CI keeps this staging for 14 days. On tag, `release-proof` keeps it
for 30 days and the separate distributions for 90 days. The manifest carries
the same retention as the upload: `CDPX_PROOF_RETENTION_DAYS`, a strict
integer from 1 to 90, defaults to 14 and to 30 in the release workflow. An
invalid value fails the proof. Outside a supervised session, local purging
is not triggered by a global daemon.

Every proof command is bounded by a deadline: a command killed at its
deadline is converted to exit 124 and produces a red verdict, never an
indefinite block. `CDPX_PROOF_TIMEOUT_SCALE`, a strictly positive float
(e.g. `2` on a slow machine), uniformly multiplies these budgets; an invalid
value blocks the proof before any destruction of the existing tree.
`CDPX_PROOF_DIR` is the internal parameter of the Symfony Compose mount
(default `./.proof`), which `./dev proof` points to its transactional
staging.

An invalid feature sheet (missing section, entrypoint without user-facing
doc, orphaned scenario) is a violation that fails the generation: the docs
cannot silently drift from the product.

```bash
PYTHONPATH=src python3 -m cdpx.proof
```

## User journeys

- Run `./dev check-local` for short feedback, `./dev check` for the full
  quality verdict, then `./dev release` before any delivery.
- Generate `.proof/proof-report.html` and `.proof/validation-summary.json`.
- Inspect feature, scenario, test and proof coverage from a single page.

## Validation

The unit tests validate the strict parsing of feature sheets (front matter,
sections, doc per entrypoint), the cockpit's Markdown rendering, the
validation summary's compatibility, the proof failure rules, the fixture
server and the CLI contract (outputs, usage errors, origin guard, subcommand
dispatch safety net). Added to that: intent extraction (docstrings, `#:`
comments, failure-line correlation, redaction), the closed artifact taxonomy
and the secondary proof helpers, the bounded payload inlining, the cockpit
assets' integrity, the "every artifact type has a viewer" guard, the native
cast recorder (pty → asciicast v2, blocking gate) and the ephemeral
screenshot banner (injection then guaranteed removal).

## Proofs

Expected proofs: JUnit reports, private local artifacts
(`.proof/proof-report.html`, `.proof/validation-summary.json`, logs and
captures), plus `.proof/shareable/` and its manifest for CI.

## Known limitations

- The short loop explicitly carries the name `./dev check-local`; `make
  check` always includes Docker, Chrome and Symfony.
- Docker/Compose missing or a skipped Symfony test: `./dev proof` and `make
  release` fail. The report keeps the `unavailable` status as a diagnostic,
  never as a degraded success.
- `SecureArtifactWriter` automatically redacts text, JSON and saved text
  files, but cannot safely inspect an opaque binary nor detect every PII.
  The canary scan remains the last staging lock.
- `.cast` files are redacted but never uploaded to the shareable staging: a
  secret can be fragmented across ndjson events and escape the scan.
- The cast player (vendored xterm.js) offers full terminal emulation; the
  scrubber's rewind replays the cast from the start (xterm has no reversible
  state), imperceptible on short demonstration casts.
- Assertion/failure correlation is silent (neutral markers) when the
  assertion fails inside a helper outside the test file: no correlation is
  better than a falsely incriminated assertion.
