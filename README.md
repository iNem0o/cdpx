# cdpx

cdpx is supervised browser automation for coding agents and the developers
steering them: it turns a disposable development Chrome into a scriptable,
measurable and policy-bound interface. Focused Chrome DevTools Protocol
actions cover rendered-page inspection, trusted user input, audits, state
control and reproducible browser evidence — proven on Symfony development,
e-commerce journeys and SEO work.

One command performs one browser action. stdout contains one JSON object,
stderr carries diagnostics, and exit codes remain stable. The same commands
run against the deterministic mock, a real Chrome and the Dockerized Symfony
reference application.

> **Version 0.1.0 — pre-1.0 beta.** The supported surface is documented and
> tested end to end. Contract changes remain possible before 1.0 and are
> recorded in the [changelog](CHANGELOG.md).

cdpx is available under the [MIT license](LICENSE). The repository is
[github.com/inem0o/cdpx](https://github.com/inem0o/cdpx).

## What cdpx does

| Usage family | Purpose | Representative commands |
| --- | --- | --- |
| **See** | Inspect the rendered page and browser activity | `text`, `html`, `console`, `network` |
| **Measure** | Read timings, metrics and Symfony diagnostics | `metrics`, `vitals`, `profiler`, `dom-diff` |
| **Audit** | Check rendered SEO, accessibility and coverage | `seo`, `a11y`, `coverage` |
| **Reproduce** | Control state, conditions and repeatable journeys | `cookies`, `storage`, `emulate`, `record`, `replay` |
| **Prove** | Capture pixels, PDFs and executable scenarios | `screenshot`, `pdf`, `scenario`, `make proof` |
| **Lock down** | Bind every action to a supervised target and policy | `session`, origin allowlists, authorities, leases |

Navigation, synchronization and trusted input form the shared foundation:
cdpx knows which page it owns, waits for useful browser state and uses the
CDP Input domain for real interactions.

cdpx is not a general computer-use layer: no vision model, no coordinate
clicking, no attachment to a personal Chrome profile and no automation of
third-party production sites. It drives disposable loopback development
browsers through DOM selectors, explicit policy and supervised sessions.

## Installation

cdpx requires Python 3.11 or newer. Chrome or Chromium is required for real
browser work; the unit tests and mock do not need a browser.

Install the released package:

```bash
python -m pip install cdpx
cdpx --version
```

Install a development checkout:

```bash
git clone https://github.com/inem0o/cdpx.git
cd cdpx
python3 -m venv .venv
source .venv/bin/activate
make setup
```

## Quickstart from a checkout

The bundled reference site keeps this walkthrough on loopback. Start it in
one terminal:

```bash
make fixtures
```

Start a supervised disposable Chrome in another terminal:

```bash
eval "$(cdpx session start \
  --run-id demo \
  --authority interaction \
  --origins "http://127.0.0.1:*" \
  --ttl 1800 \
  --export)"
```

The exported identity triple (`CDPX_SESSION`, `CDPX_RUN_ID`,
`CDPX_TARGET`) binds every command to the assigned run and page:

```bash
export FORM_NAME=Ada

cdpx goto http://127.0.0.1:8899/form.html
cdpx wait "#name"
cdpx type "#name" --secret-env FORM_NAME --clear
cdpx click "#submit-btn"
cdpx text "#result"
cdpx screenshot -o state.jpg --format jpeg
cdpx session stop
```

`make mock` provides the same supervised contract without Chrome. It prints
the exports, stays in the foreground and performs a complete teardown on
Ctrl-C.

## Command surface

The 31 commands are grouped below. `cdpx --help` documents common options,
and [docs/PRIMITIVES.md](docs/PRIMITIVES.md) provides the full contract and
examples.

| Area | Commands |
| --- | --- |
| Target and navigation | `cdpx tabs`, `cdpx version`, `cdpx goto`, `cdpx wait` |
| DOM and input | `cdpx eval`, `cdpx text`, `cdpx html`, `cdpx count`, `cdpx click`, `cdpx type`, `cdpx key` |
| Capture and observation | `cdpx screenshot`, `cdpx pdf`, `cdpx console`, `cdpx network`, `cdpx metrics` |
| State | `cdpx cookies`, `cdpx storage` |
| Audits | `cdpx seo`, `cdpx vitals`, `cdpx a11y`, `cdpx coverage` |
| Developer diagnostics | `cdpx profiler`, `cdpx dom-diff` |
| Orchestration | `cdpx intercept`, `cdpx emulate`, `cdpx frame`, `cdpx record`, `cdpx replay`, `cdpx scenario` |
| Supervision | `cdpx session` |

The eight feature specifications connect these commands to concrete
workflows:

- [Navigation and synchronization](docs/features/browser-navigation.md)
- [DOM and user actions](docs/features/dom-interaction.md)
- [Capture and observability](docs/features/browser-capture-observability.md)
- [State and session](docs/features/state-session.md)
- [SEO, performance and accessibility](docs/features/seo-performance-accessibility.md)
- [Symfony profiler and DOM diff](docs/features/dev-profiler-diff.md)
- [Interception and orchestration](docs/features/orchestration-control.md)
- [Harness and proof cockpit](docs/features/harness-proof-cockpit.md)

## Stable execution contract

- stdout is one compact JSON object; `--pretty` requests indented JSON.
- stderr contains diagnostics.
- `exit 0` means success, `exit 1` an execution failure and `exit 2` an
  invalid invocation.
- Streams and journals use compact NDJSON, one object per line.
- `--limit`, `--full` and `--max-actions` make volume and action budgets
  explicit.
- `--timeout` bounds browser and lifecycle waits.
- `--session`, `--run-id` and `--target`, or their matching environment
  variables, must identify the complete supervised assignment.

Authorities are cumulative: `observation` permits bounded reads,
`interaction` adds trusted input, and `privileged` covers JavaScript,
cookies, storage, profiler access, interception and emulation. Composed
commands are checked before the first browser effect.

Sensitive input stays out of argv and journals through `--secret-env`,
`--value-env`, `@env:NAME` and scenario `secret_ref` references. Cookie and
storage values are redacted by default.

## Security model

- The debugging endpoint stays on loopback and uses a disposable profile.
- The `CDPX_ORIGINS` allowlist is mandatory and checked before and after
  navigation.
- A single command owns the session lease at a time.
- DOM, console, network and profiler data are untrusted. Output objects carry
  `_cdpx.content_trust: "untrusted"`.
- Screenshots, PDFs and other opaque artifacts remain private unless a human
  explicitly reviews them.
- The supervisor closes the target, stops Chrome and deletes private state on
  stop, expiry or owner exit.

Read [HARNESS.md](HARNESS.md) for the normative rules and
[SECURITY.md](SECURITY.md) for private vulnerability reporting.

## Development and validation

```bash
make setup                 # editable installation and development tools
make check-local           # Ruff, formatting, mypy and unit tests
make check                 # blocking Docker, Chrome and Symfony gate
make test-e2e              # real local Chrome
make docker-symfony-e2e    # real Symfony reference application
make proof                 # private report under .proof/
make release               # check, proof and verified wheel/sdist
```

Unit tests validate both returned JSON and emitted protocol against the CDP
mock. Real Chrome and Symfony are mandatory for the release verdict. CI may
publish only the manifested `.proof/shareable/` subset; local opaque
artifacts remain private.

## Reference documentation

- [Product rationale and design](docs/CONTEXT.md)
- [Primitive catalog](docs/PRIMITIVES.md)
- [Session and Chrome lifecycle](docs/SESSION-LIFECYCLE.md)
- [Validation and proof](docs/VALIDATION.md)
- [GitHub governance](docs/GITHUB.md)
- [Release procedure](docs/RELEASING.md)
- [Contribution guide](CONTRIBUTING.md)
- [Support policy](SUPPORT.md)

The proof cockpit renders this curated documentation offline. Usage questions
and reproducible defects belong in
[GitHub issues](https://github.com/inem0o/cdpx/issues); vulnerabilities do
not.
