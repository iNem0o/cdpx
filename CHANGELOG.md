# Changelog

Format inspired by [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).
This project follows semantic versioning.

## [Unreleased]

### Added

- **Docs** gate in the proof cockpit: curated, hierarchical catalog, safe
  CommonMark rendering, eight feature specifications and offline Mermaid
  diagrams. A dedicated reference describes the full session and Chrome
  process lifecycle.
- New command `cdpx session start|status|stop`: disposable loopback browser
  profile, dynamic port, single target, private manifest, exclusive lease,
  TTL/owner and supervised teardown. The public surface goes from 30 to
  31 commands.
- `make mock` starts a supervised session in the foreground with the same
  manifest/run/target contract as real Chrome, prints the required exports
  and cleans up its resources on `Ctrl-C`.
- Authority policy `observation`, `interaction`, `privileged`; composed
  commands, replay and scenarios are preflighted at the maximum authority
  required and any unclassified capability is refused.
- Secret references: `type --secret-env`, `cookies set --value-env`,
  `record type ... @env:NAME` and `scenario type.secret_ref`.
- Cross-cutting redaction of recorded secrets, Bearer/JWT, URL/query,
  sensitive headers, console, network, profiler, errors, logs and scenarios.
  `SecureArtifactWriter` reapplies this cleanup to recorded text, JSON and
  text files. Artifacts carry classification, SHA-256, redaction policy, TTL
  and upload decision in a private manifest.

### Changed

- The cockpit now preserves its Mermaid bundle after redaction: minified
  JavaScript properties are no longer mistaken for Data URLs, and the
  pre-cleaned report traverses staging without code mutation.
- `session start --startup-timeout` now separates the Chrome cold-start
  budget (60 seconds by default, 300 maximum) from CDP timeouts. The
  supervisor and its parent share this budget without a race on expiry; CI
  runners work around their bounded `/dev/shm` and errors keep bounded,
  redacted log tails before the private teardown.
- **Breaking**: the supervised session becomes the sole execution contract.
  `--session`, `--run-id` and `--target` (or their environment variables)
  are required before discovery; direct connection via `--host`/`--port`,
  the implicit target and the optional allowlist are removed.
- **Breaking**: `tabs` exposes only `list`; creation, activation and closing
  of targets belong to the session supervisor.
- **Breaking**: `storage` now masks all values by default and exposes
  `values_masked`; `--show-values` becomes the explicit opt-in, as for
  cookies.
- **Breaking**: `type` no longer returns the typed text but
  `typed:true,value_masked:true`. `record` writes the `cdpx.record/v2`
  schema: CLI input requires `--secret-env`, `record type` requires
  `@env:NAME`, a scenario requires `secret_ref`, and `eval` stays redacted
  and non-replayable. Sensitive v1 events are refused.
- **Breaking**: screenshots, PDF, logs and scenario proofs are always
  confined under the session's private artifacts; the `scenario
  --evidence-dir` option and arbitrary output paths disappear.
- `click` now requires an element that is attached, visible, enabled,
  stable, of non-zero size and receiving the central hit-test. `type
  --clear` selects the content then emits Backspace before
  `Input.insertText`; `wait_visible` actually tests visibility. `key` now
  covers Backspace/Delete, Home/End, PageUp/PageDown, Space and the four
  arrow keys in addition to the initial set.
- Scenario console/network assertions are evaluated after a final drain.
  Scenario proofs are private, manifested and classified;
  screenshots/PDF/binaries are `opaque-restricted`.
- PR CI publishes only `.proof/shareable/` for 14 days. The release proof
  is kept for 30 days and distributions for 90 days.

### Security

- Every browser command enforces loopback, exact session/run/target
  assignment, exclusivity via lease and the `_cdpx.content_trust:"untrusted"`
  metadata on outputs. Real destinations and origins are checked fail-closed.
- `replay` re-reads `window.location.href` after each navigation and before
  the next mutation: a redirect to a forbidden origin can no longer receive
  the next click.
- The interception parser refuses any action other than `continue`, `block`
  or an HTTP status `200..599`; a typo no longer silently continues the
  request.
- Public discovery outputs no longer contain debugging WebSocket URLs. Proof
  staging excludes opaque files and fails closed if a known canary remains.

## [0.2.0] — 2026-07-11

### Changed

- The project is now published under the MIT license, with inem0o as the
  copyright holder established for 2026.
- GitHub becomes the project's main public platform at
  `https://github.com/inem0o/cdpx`.
- GitHub Actions calls the Make gates with minimal permissions and pinned
  actions; PyPI publishing is prepared via Trusted Publishing OIDC.
- Validation Docker images are pinned by digest and tracked by Dependabot.
  `.proof/` proofs become unversioned CI artifacts.
- The wheel and sdist are inspected before a clean wheel install; Symfony's
  MIT notice accompanies the derived WebProfiler fixtures.
- The standard `make check` gate now requires Docker, Chrome and Symfony;
  the short loop is explicitly `make check-local`. `make release` adds a
  green proof cockpit with no Symfony skip and the wheel/sdist artifacts.
  Docker/Symfony unavailable is no longer a degraded success for
  `make proof`.
- The validation image embeds the packaging metadata and the full `.[dev]`
  tooling; CI runs Chrome, Symfony and proof on merge request, tag and
  scheduled pipeline before the build job.
- The distribution tooling requires a `packaging` version compatible with
  the PEP 639 metadata (`License-Expression`/`License-File`) produced by
  recent setuptools.
- **Breaking**: `tabs list` now returns `{tabs, count}` instead of a root
  list, which makes `--limit` effective and keeps stdout as a JSON object
  for all commands.
- The `CDPX_ORIGINS` guard now also covers cookies, `vitals --click`, the
  interception destination and every mutation replayed after navigation.
  `replay` validates the whole log before acting and compares recorded
  results.
- CDP navigation errors become exit 1, SEO accepts array/scalar JSON-LD
  roots, proofs mask sensitive headers and JS coverage exposes used/unused
  bytes per resource.

- **Breaking**: `cdpx profiler` now parses the real WebProfilerBundle HTML
  panels (db, twig, cache, exception, http_client, messenger, router, time,
  logger) retrieved via `fetch()` in the page. `panels` is a structured
  object per panel (`available`/`parse_error`, never a parsing exception);
  new option `--panels all|none|list`.
- **Breaking**: removal of the `signals` fields (fabricated
  `X-CDPX-Profiler-*` headers) and `profiler_bytes` from `cdpx profiler`
  output and from the scenarios' `profiler` artifact: metrics now come from
  the real panels, no more fixture signals.

## [0.1.0] — 2026-07-05

Initial release.

### Added

- 30 CLI subcommands on the Chrome DevTools Protocol, organized into 8
  documented features (navigation, DOM/actions, capture/observability,
  state/session, SEO/perf/a11y audits, Symfony diagnostics, orchestration,
  harness/proof). Stable contract: stdout = a JSON object, stderr =
  diagnostics, exit 0/1/2.
- Real replay of journeys: `record` executes and logs each action (NDJSON),
  `replay` validates the log then replays it against the browser and stops
  at the first divergence.
- Composed form `emulate <preset> -- <action>`: act under emulation within
  the same CDP connection (overrides die with the connection).
- `screenshot --format png|jpeg`; `emulate --reset` also restores the
  user-agent; `profiler_status` reflects the profiler's real HTTP status.
- `CDPX_ORIGINS` origin guard extended to composed commands (classified by
  action verb) and to `replay`.
- Proof cockpit `make proof`: embedded per-feature user documentation,
  Features / CLI / Validation / Gaps / Run views, explicit Symfony policy
  (`CDPX_PROOF_REQUIRE_SYMFONY=1`), narrative debt ratchet at 0.
- Mechanical documentation guards (`tests/test_docs.py`): all commands
  documented, sheets routed from the README, `cdpx` examples validated
  against the real parser.
- Packaging: single version (`cdpx.__version__`), wheel+sdist via
  `make dist`, proprietary license, `dev` extras, coverage with threshold,
  mypy `typecheck` target, GitLab CI matrix on 3.11/3.12 with artifacts.
