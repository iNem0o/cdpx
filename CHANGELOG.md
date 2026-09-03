# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
cdpx uses semantic versioning.

## [Unreleased]

### Added

- `vitals` now reports a versioned `cdpx.vitals/v3` result: collector
  availability (`status`, `document_observed`, `arm_scope`, `supported`,
  `dropped_entries`, `errors`), per-metric availability, interaction record,
  document binding (requested/final URL, time origin, navigation
  source/step, main-frame scope, viewport, capture timestamp, browser
  version) and bounded winning-window entries with DOM sources and
  before/current rectangles. The collector runs inside a CDP isolated world:
  page JavaScript cannot falsify it. A capture without an observable
  collector reports `status: "unavailable"` with a reason instead of a
  silent zero.
- Scenarios can capture `vitals`, use a bounded `wait_ms`, and apply existing
  interception rules around journey navigations and trusted clicks while
  reporting matched/effective counts.
- `eval --file` and `eval --stdin` accept UTF-8 scripts up to 1,000,000 bytes
  and report source kind plus SHA-256 without echoing the script.

### Changed

- **`vitals` contract v3 (migration from `cdpx.vitals/v2`):** `metrics` is
  now a map of per-metric availability entries (`{"status": "measured" |
  "unsupported", "value": ...}`; the `cls` entry keeps `raw_sum`,
  `total_entries`, `ignored_recent_input` and `winning_window`), so an entry
  type the browser does not implement is reported `"unsupported"` with a
  null value instead of a silent zero. `status` gains `"partial"` for
  browser-announced dropped performance entries (`partial_reasons`, metrics
  attached) beside `"measured"` and `"unavailable"`; arming/reading failures
  now fail the command instead of being reported as `"unavailable"`.
  `collector` replaces the always-true `installed` flag with `arm_scope`
  (`document-start` | `capture-time`) and `dropped_entries`; `interaction`
  distinguishes a requested click that produced no observable entry
  (`requested`/`observed`/`entry_count`); `document` reads the final URL
  atomically with the metrics, adds `performance.timeOrigin` and, in
  scenarios, keeps the requested URL distinct from the displayed one with
  the navigation source (`goto`/`click`/`redirect`/`current-document`) and
  the step that produced it. The standalone `cdpx vitals` command arms the
  collector BEFORE the optional `--click` (the Event Timing observer is live
  during the interaction) and judges the real origin immediately after the
  navigation and the interaction — a forbidden document is never measured;
  scenario vitals captures register the collector before the first
  navigation and embed a `measurement_environment` block (emulation,
  interception rules, scenario digest).
- **`vitals` CLS semantics (migration from v1):** before this change the top-level
  `cls` field was a raw sum of layout-shift entries; it is now the official
  maximum session window. Old `cls` value → new `metrics.raw_sum`; new
  `metrics.cls` → official maximum session window over eligible entries
  (`hadRecentInput` excluded from both). The `schema` field lets consumers
  distinguish the contracts. `metrics.lcp` and `metrics.inp` are documented
  as approximate signals, not the official LCP/INP algorithms.
- Interception hits are bounded at the source: recorded hits are capped per
  action (URLs capped too) while `hits_total`/`hits_limit`/`hits_truncated`
  keep the exact totals; scenario step results no longer duplicate the
  bounded aggregate.
- A `vitals` settle wait no longer consumes buffered console/network events
  owed to passive collectors.
- Named keys accept unambiguous case-insensitive aliases and report their
  canonical spelling.

## [0.2.0] — 2026-08-30

### Added

- Scenario `profiler` captures now accept explicit panels and an optional
  request selector over path prefix, Document/XHR/Fetch type and HTTP method.
  Explicit selectors fail closed instead of silently profiling the current
  document, while artifacts record safe selection metadata.
- `cdpx profiler` now probes advertised collector IDs and reports a bounded
  `profile` descriptor. A composable Shopware adapter selects the DAL
  collector directly and exposes active rules and cache-tag emissions through
  stable semantic panels.
- `cdpx profiler` now exposes lightweight `shopware_feature_flags` by default
  and bounded `shopware_cart` diagnostics only through an explicit panel or
  `--panels all`. Cart keeps localized display strings and pipeline priorities
  while deliberately omitting hidden dumps, payloads and serialized objects.
- Database profiler output now extracts leading Shopware DAL query tags and
  their best-effort source location without changing the existing query and
  repetition lists.
- Versioned `cdpx.scenario/v1` journeys can compose nested local
  `cdpx.scenario-fragment/v1` step files through explicit `include` nodes.
  Expansion is deterministic, workspace-confined and cycle/budget guarded;
  run results carry qualified labels, source provenance, dependency hashes
  and a composition digest.
- `cdpx scenario validate <file.yml>` compiles and inspects a complete
  scenario without a browser session or secret materialization, reporting
  its ordered plan, required authority and referenced environment names.
- Public JSON Schemas describe executable scenarios and reusable fragments.
- `cdpx intercept` can compose a trusted `click <selector>` as well as
  `goto <url>`, report matched and effective request counts, and explicitly
  disable Fetch after success or failure so interception cannot leak into the
  next action.

### Changed

- Profiler panel selection is now defined by reusable panel specs: an omitted
  `--panels` selects lightweight defaults, while `--panels all` also fetches
  extended panels whose collector IDs were advertised by the common probe.
- Click interception now validates top-level document destinations before a
  rule can affect them, and short or zero `--settle` periods drain buffered
  requests without overrunning their quiet deadline.
- Runtime and development images now pin Chromium 151.0.7922.173 from Debian
  Bookworm security after the previous pinned package left the repository.

### Fixed

- Composed `type` actions now consume one timeout across actionability,
  preparation, optional clearing and trusted key events, so a long referenced
  secret cannot continue dispatching characters after its action budget. The
  same deadline also bounds the current-origin checks between key events.
- Scenario validation now reports non-string keys inside `frame_type`
  candidates as exit-2 usage errors instead of leaking an internal traceback.
- Direct Symfony and Shopware gates now pass the invoking UID/GID into Compose,
  preserving ownership of their bind-mounted proof artifacts on Linux hosts.
- Paced `frame_type` input now consumes the scenario timeout across frame
  discovery, focus, origin guards, keyboard events and delays, and invalid
  typing options fail before the hosted frame receives a click. Declared frame
  origins must be concrete HTTP(S) origins, and printable punctuation now emits
  its physical US-key metadata and modifiers.
- Click interception now explicitly continues paused requests buffered after a
  zero-settle snapshot, including events delivered while Fetch is being
  disabled, preventing cleanup from leaving an observed request without a
  protocol decision.
- Declarative scenarios now expand `${NAME}`, `${NAME:-default}` and `$$` in
  `context.base_url` before strict HTTP(S) origin preflight. Missing variables
  fail as usage errors without exposing their values; scenario secrets remain
  on the existing `secret_ref` path.
- The embedded installer now resolves its public `/opt/cdpx/install` symlink
  before locating the bundle, so it links `cdpx` to the bundled native entry
  point instead of the nonexistent `/opt/bin/native-cdpx` path.

## [0.1.4] — 2026-07-23

### Added

- `runtime.trust_ca` in `cdpx.yaml` lists workspace CA certificates (PEM),
  bind-mounted read-only and imported into a per-session trust store at
  `session start`, so a supervised Chrome trusts a local development
  authority (`mkcert`, traefik) instead of failing with
  `ERR_CERT_AUTHORITY_INVALID`. Copy only `rootCA.pem`; a file containing a
  `PRIVATE KEY` block is rejected at compilation. The runtime image now
  bundles `certutil` (via `libnss3-tools`) to perform the import.
- `session.ignore_tls_errors` in `cdpx.yaml` and the matching
  `cdpx session start --ignore-tls-errors` flag launch Chrome with
  `--ignore-certificate-errors`, a dev-only fallback for local HTTPS behind
  an untrusted development CA.

### Changed

- **Breaking**: session manifests move from `cdpx.session/v2` to
  `cdpx.session/v3`. An active session created by an older version fails
  closed; clear it with `cdpx runtime reset --force`.

## [0.1.3] — 2026-07-22

### Added

- `runtime.extra_hosts` in `cdpx.yaml` maps hostnames to an IP address or
  to `host-gateway` (`--add-host`), so a runtime joined to a development
  stack network resolves names the stack only registers in the host's
  `/etc/hosts`.
- Environment interpolation in `cdpx.yaml` values: `${NAME}`,
  `${NAME:-default}` and `$$` resolve against the calling environment at
  plan compilation, letting stack tooling drive the network name and
  extra hosts through exported variables.

### Changed

- **Breaking**: `$` is now reserved in every `cdpx.yaml` string value.
  A literal `$` accepted by earlier releases must be escaped as `$$`;
  any other bare `$` fails compilation with a `malformed placeholder`
  error.

### Documentation

- The README, homepage and installation guide now separate installed mode
  (launcher deployment, updates, constraints, uninstall) from dev mode
  (contributing through `./dev`), and a "How cdpx runs" section
  disambiguates the installed launcher, the in-image CLI and the
  contributor harness. The homepage version badge is corrected and now
  covered by the release version-pin test.

## [0.1.2] — 2026-07-21

### Added

- A public agent-assisted onboarding guide and reusable `cdpx` skill for safe
  project setup, supervised browser use and troubleshooting.

## [0.1.1] — 2026-07-19

### Fixed

- The released launcher refused to run: the release digest substitution
  also rewrote the unreleased-guard pattern, so every published launcher
  matched its own digest. The substitution is now anchored to the
  `DEFAULT_IMAGE` line and the launcher test bakes a digest exactly as
  the release workflow does.

## [0.1.0] — 2026-07-19

### Added

- 31 supervised Chrome DevTools Protocol commands covering navigation, DOM
  interaction, capture, observation, state, rendered-page audits, Symfony
  diagnostics and repeatable browser journeys.
- Disposable loopback Chrome sessions with an exact session/run/target
  identity, origin allowlists, authority levels, exclusive leases and bounded
  teardown.
- Deterministic CDP mock tests, real-Chrome scenarios, a Dockerized Symfony
  reference application and a private proof cockpit.
- One pinned, multi-stage OCI toolchain for development, validation, release
  and the production runtime, exposed locally through the Docker-only `./dev`
  portal.
- A digest-pinned `cdpx` host launcher that manages one hardened runtime per
  working tree, validates `cdpx.yaml`, and exposes runtime lifecycle commands.
- Multi-architecture OCI releases for amd64 and arm64, plus an optional
  relocatable embedded Linux artifact for environments that cannot run
  containers.
- Normative user and integrator documentation for installation, configuration,
  development, runtime integration, release architecture and troubleshooting.

### Changed

- The public distribution is now the signed-off OCI image promoted by digest;
  PyPI wheels and source archives are internal build evidence only.
- Session manifests use `cdpx.session/v2` and attest the runtime identity.
- Python 3.14 is the single interpreter baseline across local development, CI
  and release.

### Security

- Cookie, storage and sensitive input values are redacted by default.
- Page, console, network and profiler content is marked as untrusted.
- Browser writes remain inside private session artifact directories; opaque
  files are excluded from automatic sharing.
- The production runtime is read-only, capability-free, protected by
  `no-new-privileges`, and receives configuration through a mode-0600
  environment file rather than command-line secrets.
