# VALIDATION.md

Reproducible proof of cdpx milestones. Outputs stay compact for agents:
JSON stdout when useful, private logs in `.proof/`, and heavy checks
explicitly separated. `.proof/` is not versioned. Only its manifested
staging `.proof/shareable/`, cleaned and free of opaque artifacts, is
publishable by GitHub Actions.

## Gates

- `make check-local`: browser-free development sub-gate: lint, format,
  mypy and deterministic unit tests — including the documentation
  guards (`tests/test_docs.py`: every command documented in README and
  PRIMITIVES, every sheet routed, every `cdpx` example parsed against
  the real parser).
- `make check`: standard, blocking quality gate: `check-local`, then
  the same check in the Docker image, real Chrome in Docker and real
  Symfony.
- `make test-e2e`: real Chrome scenarios against the local fixtures.
- `make mock`: foreground supervised session with a simulated CDP
  backend; the associated unit scenario proves manifest, identity
  triple and teardown without real Chrome.
- `make docker-check`: `make check-local` in the portable `cdpx-ci`
  image.
- `make docker-e2e`: real Chrome in the `cdpx-ci` image.
- `make docker-symfony-e2e`: profiler e2e against a real Symfony
  Docker app.
- `make proof`: collects lint, format, unit/integration tests, Chrome
  e2e, Symfony e2e (Docker), CLI help, JUnit XML, logs, pytest
  scenarios and e2e screenshots, then writes
  `.proof/proof-report.html` and `.proof/validation-summary.json`.
  The full local tree stays private; a second step builds the
  shareable staging and scans the canaries.
- `make release`: aggregated blocking gate. It requires `check`, the
  Docker checks, real Chrome, real Symfony without skipping, the
  complete proof and the wheel/sdist artifacts. `check-local` alone
  never constitutes a release verdict.
- `make dist`: builds wheel and sdist, applies `twine check --strict`,
  checks required/forbidden contents, then installs the wheel in a
  temporary venv to verify the license, the help and the 31 commands.

## The proof report

`.proof/proof-report.html` is a browsable single-page application,
designed as the human documentation of the product:

- **Features**: complete user documentation for each feature
  (generated from `docs/features/*.md`), journeys, given/when/then
  scenarios, executed tests, proof (real Chrome screenshots).
- **Docs**: catalog curated by `docs/cockpit.toml`, navigation
  matching the repository hierarchy, safe CommonMark and Mermaid
  diagrams rendered offline.
- **CLI**: complete command surface and entrypoint → feature
  attachment. An unattached public entrypoint is a blocking
  violation.
- **Validation**: milestone → proof matrix (table below), tests per
  module, risks/mitigations, assumed unknowns.
- **Gaps**: (blocking) violations and catalog warnings. The budget of
  tests attached by a feature glob without a documented scenario is a
  ratchet at 0.
- **Run**: run commands, JUnit suites, failing or slowest tests,
  collapsible log tails.

All managed files are written under `0700` directories and in `0600`
mode. The `cdpx.artifacts/v1` manifest carries SHA-256, classification,
upload authorization, redaction version and expiration. Cleaned text
is `internal`; screenshots, PDFs and binaries are `opaque-restricted`
and are never copied into the staging. The canary scan fails closed
before upload. The proof manifest carries the effective publication
TTL: 14 days by default and on PRs, 30 days on a tag. The
`CDPX_PROOF_RETENTION_DAYS` variable accepts only an integer from 1 to
90; an invalid value blocks the proof before replacing the existing
tree. This TTL is purgeable retention data, not an automatic deletion
daemon.

Every proof command is bounded by a deadline: a command killed at its
deadline becomes an exit 124, hence a red verdict, never an indefinite
block. `CDPX_PROOF_TIMEOUT_SCALE` (a strictly positive float)
uniformly multiplies these budgets; an invalid value blocks the proof
before any destruction of the existing tree. `CDPX_PROOF_DIR` is the
internal parameter for the Symfony Compose mount, default `./.proof`.

Raw HTML from Markdown sources is disabled. Internal links resolve
only to published documents, and Mermaid runs with `securityLevel:
strict`, a CSP with no outbound connection and a local bundle whose
fingerprint is verified before the report is generated.
The dynamic summary is redacted before HTML rendering. The verified
JavaScript bundle is then not run back through the free-text
detectors — which could mistake a minified property for a URL — while
the final canary scan stays blocking on the shareable staging.

Symfony policy: Docker, Compose and the real Symfony suite are
mandatory for any release proof. An `unavailable` proof or a skipped
Symfony test makes the verdict red. There is no degraded release
success without Docker. `make check-local` only shortens the
development loop; the standard `make check` gate remains complete.

GitHub Actions workflows call these Make targets rather than
rewriting their logic. A GitHub runner result remains required before
tagging, even when the same commands succeeded locally.

## Proof in GitHub Actions

The `CI` workflow runs on every pull request, with no path filter. It
includes the Python 3.11/3.12 compatibility jobs and a complete gate
that calls `make release`. The latter successively covers lint,
format, mypy, unit tests, Docker, real Chrome, real Symfony, cockpit,
wheel/sdist, `twine check --strict`, archive contents, isolated wheel
installation and counting the 31 commands.

The stable **`PR Gate / Required`** check depends on all of these
jobs. It fails if any of them fails, is cancelled or is skipped. It
is the only name meant for `master` protection; a matrix change
therefore never changes the rule. PR checkboxes never replace this
executed result.

The **Full release gate** job publishes, in its *Summary* tab, a
table derived from `.proof/validation-summary.json`, the actual
outcome of `make release` and the archives actually present. It shows
verdict, SHA, version, tests passed/failed/skipped/unavailable,
Chrome, Symfony, CLI commands, catalog, packaging and artifact name.
No number is hardcoded, except the expected public contract of 31
commands.

The `pr-proof-<run-id>-<attempt>` artifact is kept for **14 days**
and published with `if: always()`. It takes exclusively
`.proof/shareable/`; a missing staging is an upload error. Depending
on the point of failure, it contains the available manifested text
files, notably:

- `proof-report.html` and `validation-summary.json`;
- the unit, Chrome and Symfony JUnit files;
- the redacted text logs produced by the cockpit: Ruff, mypy, pytest
  and Docker/Chrome/Symfony;
- the text scenarios and metadata under `.proof/evidence/`;
- `artifact-manifest.json`, which also lists the opaque files kept
  locally but deliberately excluded from the upload.

The gate's raw log, the local proof's screenshots/PDFs/binaries and
the distributions are not in this PR artifact. On tag,
`release-proof` keeps a manifested staging with a matching TTL of 30
days; wheel and sdist are published separately in
`python-package-distributions` for 90 days.

From the GitHub run, download the artifact in the *Artifacts*
section. From the CLI: `gh run download <RUN_ID> -n
pr-proof-<RUN_ID>-<ATTEMPT>`. Start with `validation-summary.json`,
then open `proof-report.html`, then the JUnit or the log of the red
layer. On a failure before the staging is built, the upload itself
may report the missing file: the job log and the GitHub summary then
remain the available diagnostics. No heavy suite is rerun just to
produce an artifact.

First reproduce with the target indicated (`make check-local`, `make
docker-e2e`, `make docker-symfony-e2e`, `make proof` or `make dist`),
then with `make release`. After fixing, a maintainer can rerun the
failed jobs from *Re-run jobs* or with `gh run rerun <RUN_ID>
--failed`. A rerun produces a new artifact suffixed with its attempt
number.

GitHub rules, their verification and the diagnosis of a merge block
are centralized in [GITHUB.md](GITHUB.md).

## Matrix

| Milestone | Proof |
| --- | --- |
| M0 foundation | `make check-local`, mock CDP validating outputs, methods, params and order |
| M1 real Chrome | `make test-e2e`, full Blink/V8 suite on the same fixtures |
| M2 Symfony | `make docker-symfony-e2e`, profiler extraction via a real header |
| M3 interception | unit + e2e Fetch continue/fulfill/block, timing settle |
| M4 SEO/perf | vitals with interaction, a11y AXTree, JS/CSS coverage, SEO edge cases |
| M5 orchestration | record/replay with divergence, frame, allowlist, max-actions |
| M6 distribution | `make docker-check`, `make docker-e2e`, `cdpx-ci` image |
| M8 sessions/security | policy/session/journal/redaction/artifacts unit tests + supervised mock session + real Chrome multi-session E2E |
| Release | `make release`, all previous gates + proof + wheel/sdist |

## Covered edge cases

- No Chrome: explicit e2e failure, with no false success via skip.
- No Docker/Compose or a skipped Symfony: explicit failure of the
  proof and the release gate.
- E2e proof: every non-skipped Chrome scenario must expose at least
  one screenshot in `.proof/evidence/`.
- Cookies: `Storage.clearCookies` with a fallback to the earlier CDP
  method.
- Interception: encoded fulfill response, network block, continue,
  invalid rule.
- Replay: journal v1/v2, missing secret ref before a CDP effect,
  invalid NDJSON, missing action, `ok:false` divergence, budget,
  semantic comparison and real origin re-read after
  redirection/before mutation.
- Interception: only the `continue`, `block` actions or `200..599`
  statuses are accepted; an unknown action fails before navigation.
- SEO: invalid JSON-LD, incomplete Product, duplicated H1s, estimated
  lengths.
- Sessions: session/run/target required before discovery, no
  implicit endpoint or target, private manifest/lease, mock backend
  under the same contract, three isolated simultaneous Chrome
  profiles, teardown and authority matrix.
- Origins: mandatory fail-closed allowlist, destinations and real
  origin checked before/after navigation and before action.
- Agentic outputs: compact JSON by default, `--limit`/`--max-actions`
  limits, NDJSON for streams, redacted cookies/storage/inputs,
  cleaned URL/query/headers/console/profiler and page content marked
  untrusted.
- Interactions: `wait_visible` distinguishes visibility from DOM
  presence; `click` refuses detached, hidden, disabled, unstable or
  covered elements; `type --clear` selects then emits Backspace
  before `Input.insertText`; the extended keyboard is locked down by
  mock and by Home/Delete/End/Space on real Chrome.
- Scenarios: final console/network drain before assertions and a
  missing `secret_ref` rejected before action.

## Non-blocking debt

- `KEY_MAP` remains deliberately bounded to the tested named keys;
  any extension requires a need, a mock and a Chrome scenario.
- `eval` remains a monitored escape hatch; repeated use gets promoted
  to a named primitive.
- `a11y` is a compact AX view, `vitals` a bounded local measurement,
  `seo` an on-page check, `network` a non-HAR summary and `replay` a
  partial comparison: none of these signals should be presented as
  exhaustive.
- The TTL of local proofs is manifested and purgeable, but no global
  daemon currently triggers their periodic purge.
- `key` has dedicated Chrome scenarios. `tabs list` is tested under
  an assigned session; the lifecycle of targets is covered by the
  supervisor scenarios and is not part of the public CLI surface.
