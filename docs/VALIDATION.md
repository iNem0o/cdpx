# Validation and proof

cdpx produces reproducible, private evidence for every release gate. Compact
JSON is used where it helps automation, detailed logs stay under `.proof/`,
and only the manifested `.proof/shareable/` subset may be uploaded.

## Gates

| Command | Purpose |
| --- | --- |
| `./dev check-local` | Ruff, format, mypy, deterministic unit and branch coverage |
| `./dev test-e2e` | real Chrome against the reference fixtures |
| `./dev check` | blocking Chrome, Symfony and proof gate |
| `./dev proof` | the same full gate with private cockpit and shareable staging |
| `./dev release` | full gate plus internal wheel build |

Unavailable Docker, Chrome, Compose or Symfony is a failure for `./dev check`,
`./dev proof` and `./dev release`. `./dev check-local` is the browser-free
development loop, not a release verdict.

Unit coverage is measured with branches enabled. The gate requires at least
85% line coverage and 75% branch coverage; the thresholds are evaluated
independently from `.coverage.json`.

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

Local image tags and the Symfony Compose project are scoped by the canonical
worktree path. The Compose proof mount resolves to that worktree's
transactional staging directory, and an advisory lock refuses overlapping
proof writers inside one worktree. Gates from distinct worktrees may run in
parallel, subject to host CPU and memory capacity.

## GitHub Actions

Pull requests run the containerized short gate natively on amd64 and arm64,
the portable launcher smoke test on macOS, and the complete Chrome/Symfony
gate on amd64. **`PR Gate / Required`** aggregates them and fails when a
dependency is failed, cancelled or skipped.

The **Full release gate** summary is generated from
`.proof/validation-summary.json` and the archives actually present. Pull
request proof is retained for 14 days, release proof for 30 days and
distributions for 90 days. Start diagnosis with the workflow summary, then
the validation JSON, cockpit, JUnit and bounded text logs.

## Capability matrix

| Capability | Proof |
| --- | --- |
| CLI and mock foundation | `./dev check-local`; mock output, method, parameter and ordering assertions |
| Real browser behavior | `./dev test-e2e` and the full proof |
| Symfony diagnostics | `./dev check` with real profiler panels |
| Interception and emulation | Fetch-domain and emulation unit plus Chrome scenarios |
| SEO, performance and accessibility | rendered SEO cases, vitals interaction, AX tree and coverage |
| Journey orchestration | record/replay divergence, frames, scenarios and action budgets |
| Session and security | identity, policy, redaction, lease, isolation and teardown tests |
| Distribution | native image/bundle smoke tests and internal wheel inspection |
| Release | exact-candidate digest promotion after every capability above is green |

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
