# Validation and proof

cdpx produces reproducible, private evidence for every release gate. Compact
JSON is used where it helps automation, detailed logs stay under `.proof/`,
and only the manifested `.proof/shareable/` subset may be uploaded.

## Gates

| Command | Purpose |
| --- | --- |
| `make check-local` | Ruff, format check, mypy and deterministic unit tests |
| `make docker-check` | the same short gate in the pinned validation image |
| `make test-e2e` | real local Chrome against the reference fixtures |
| `make docker-e2e` | real Chrome in the validation image |
| `make docker-symfony-e2e` | real profiler scenarios against the Symfony app |
| `make proof` | private cockpit, summaries, JUnit, logs and shareable staging |
| `make dist` | wheel/sdist, strict metadata, content and clean-install checks |
| `make check` | blocking local, Docker, Chrome and Symfony gate |
| `make release` | `check`, proof and verified distributions |

Unavailable Docker, Chrome, Compose or Symfony is a failure for `make check`,
`make proof` and `make release`. `make check-local` is the browser-free
development loop, not a release verdict.

## Proof cockpit

`.proof/proof-report.html` is an offline report with these views:

- **Features:** current feature documentation, scenarios, tests and evidence.
- **Docs:** the curated catalog from `docs/cockpit.toml`.
- **CLI:** command coverage and command-to-feature attachment.
- **Validation:** capability-to-proof matrix, tests, risks and unknowns.
- **Gaps:** blocking catalog violations and warnings.
- **Run:** commands, JUnit suites, failures, slow tests and bounded log tails.

Raw HTML from Markdown is disabled. Internal links resolve only to cataloged
documents. Mermaid uses a verified local bundle, strict security mode and a
CSP without outbound connections.

Managed proof files use private permissions and a `cdpx.artifacts/v1`
manifest containing classification, SHA-256, upload decision, cleanup policy
and expiration. Text is cleaned before staging. Screenshots, PDFs and other
opaque files are `opaque-restricted` and never enter the shareable subset.
The final canary scan fails closed.

`CDPX_PROOF_RETENTION_DAYS` accepts an integer from 1 to 90.
`CDPX_PROOF_TIMEOUT_SCALE` accepts a strictly positive number and scales every
bounded proof command. Invalid values fail before the current proof tree is
replaced. `CDPX_PROOF_DIR` selects the internal Compose mount and defaults to
`./.proof`.

## GitHub Actions

Pull requests run Python 3.11/3.12 compatibility jobs and the complete release
gate. **`PR Gate / Required`** aggregates them and fails when a dependency is
failed, cancelled or skipped.

The **Full release gate** summary is generated from
`.proof/validation-summary.json` and the archives actually present. Pull
request proof is retained for 14 days, release proof for 30 days and
distributions for 90 days. Start diagnosis with the workflow summary, then
the validation JSON, cockpit, JUnit and bounded text logs.

## Capability matrix

| Capability | Proof |
| --- | --- |
| CLI and mock foundation | `make check-local`; mock output, method, parameter and ordering assertions |
| Real browser behavior | `make test-e2e` and `make docker-e2e` |
| Symfony diagnostics | `make docker-symfony-e2e` with real profiler panels |
| Interception and emulation | Fetch-domain and emulation unit plus Chrome scenarios |
| SEO, performance and accessibility | rendered SEO cases, vitals interaction, AX tree and coverage |
| Journey orchestration | record/replay divergence, frames, scenarios and action budgets |
| Session and security | identity, policy, redaction, lease, isolation and teardown tests |
| Distribution | strict wheel/sdist inspection and isolated wheel installation |
| Release | `make release` with every capability above and a green proof verdict |

## Required edge cases

- Explicit failure when Chrome, Docker or Symfony is unavailable.
- PUT tab creation with bounded GET fallback only after a method rejection.
- Cookie clearing through the browser-supported domain fallback.
- Interception restricted to `continue`, `block` or status `200..599`.
- Replay preflight, action budget, origin re-read and first-divergence stop.
- Scenario assertion drain and secret-reference refusal before browser effect.
- Visible, enabled, stable and unobscured interaction targets.
- Mandatory session/run/target identity and fail-closed origin allowlist.
- Redacted cookies, storage, input, URLs, headers, console and profiler data.
- Opaque evidence confined to private artifacts.

The signals remain bounded diagnostics: accessibility output is a compact AX
view, vitals are a local measurement, SEO is an on-page inspection, network
is not a HAR implementation and replay compares a defined subset.
