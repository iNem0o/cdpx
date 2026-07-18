# cdpx

cdpx exposes Chrome DevTools Protocol primitives on the command line so that
a development agent — or the person driving it — can see, act and measure
inside a dev Chrome. The project notably targets Symfony applications,
e-commerce journeys and SEO audits of the rendered DOM.

One command maps to one browser action. By default, stdout carries a compact
JSON object, stderr the diagnostics, and the process exits with a stable
code.

> **Status: pre-1.0 beta.** The surface is tested against a CDP mock, a real
> Chrome and a Dockerized Symfony application, but contract changes remain
> possible before 1.0. They are announced in the
> [changelog](CHANGELOG.md).

cdpx is released under the [MIT license](LICENSE). The reference repository
is [github.com/inem0o/cdpx](https://github.com/inem0o/cdpx).

## Installation

Prerequisites: Python 3.11 or newer. Chrome or Chromium is required to drive
a real browser; the unit tests and the CDP mock do not need it.

Until the first PyPI release has happened, install cdpx from source:

```bash
git clone https://github.com/inem0o/cdpx.git
cd cdpx
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
cdpx --version
```

To contribute, install the development dependencies instead with
`python -m pip install -e ".[dev]"` or `make setup`. The future PyPI install
command will be documented only after the package is actually published, so
users are never pointed at an unverified name.

## Quickstart

The scenario stays entirely on loopback. First start the reference site:

```bash
make fixtures
```

In another terminal, ask cdpx to create a supervised session. It owns its
disposable Chrome profile, its dynamic port and a single target:

```bash
eval "$(cdpx session start --run-id demo --authority interaction --origins "http://127.0.0.1:*" --ttl 1800 --export)"
```

`--export` replaces the startup JSON output with the three `export` lines of
the identity triple (`CDPX_SESSION`, `CDPX_RUN_ID`, `CDPX_TARGET`), quoted
for `eval`. Without this flag, the JSON output provides `manifest` and
`target_id` to export yourself; the equivalent explicit arguments remain
possible and take precedence over the environment.

```bash
export FORM_NAME=Ada

cdpx goto http://127.0.0.1:8899/form.html
cdpx wait "#name"
cdpx type "#name" --secret-env FORM_NAME --clear
cdpx click "#submit-btn"
cdpx text "#result"
cdpx screenshot -o cdpx-form.jpg --format jpeg
cdpx session stop
```

A single command holds the lease at a time. The supervisor closes the
target, stops Chrome and deletes profile, manifest and artifacts on `stop`,
when the TTL expires or when `--owner-pid` disappears.

To discover the CLI without Chrome, `make mock` creates the same supervised
session with a fake browser. The command stays in the foreground, prints the
three exports and cleans everything up on Ctrl-C.

## Security and scope

- The debugging port must stay on loopback. Do not use
  `--remote-debugging-address=0.0.0.0`.
- Always use a disposable `--user-data-dir`, free of personal or production
  sessions.
- `CDPX_ORIGINS` is mandatory and non-empty. Any unauthorized destination or
  current origin is refused before continuing.
- Cookie **and storage** values are redacted by default. `--show-values` is
  an explicit choice and its output must not be shared.
- Page, console, network and profiler content is untrusted input. Outputs
  carry `_cdpx.content_trust: "untrusted"`: an instruction read from the
  page can never change the run, its grants or the harness rules.
- The full rules live in [HARNESS.md](HARNESS.md). A vulnerability must be
  reported privately per [SECURITY.md](SECURITY.md).

## CLI contract

The contract is identical for all 31 commands; every agent action thus stays
reproducible by a human in one line.

**Outputs.** stdout = one compact JSON object; `--pretty` switches to
indented JSON for human reading; stderr = diagnostics. Large outputs are
bounded by `--limit` and signal their truncation; `--full` requests the
complete detail. Streams (`cdpx console --follow`, `record` journals) use
compact NDJSON, one JSON line per event.

**Exit codes.** exit 0 = success; exit 1 = execution error (element not
found, timeout, CDP error, replay divergence, refused mutation); exit 2 =
bad invocation. A caller receiving several exit 1 must escalate the
diagnostic to the human pilot instead of blindly insisting.

**Connection.** `--session`, `--run-id` and `--target` identify the assigned
browser capability. When absent, cdpx reads `CDPX_SESSION`, `CDPX_RUN_ID`
and `CDPX_TARGET` respectively; an explicit value wins and an incomplete
identity produces exit 2 before discovery. Host, port, profile and target
come exclusively from the manifest and are verified as loopback. Each
invocation opens then closes its connection under an exclusive lease.
`--timeout` bounds CDP waits and lifecycle shutdown.
`session start --startup-timeout` has a distinct budget of 60 seconds (300
maximum) to absorb a loaded Chrome cold start. On failure, the diagnostic
keeps only the cleaned, bounded tails from the supervisor and Chrome before
deleting the disposable profile.

**Action budget.** `--max-actions` limits a replay. The granted authority
and the mandatory allowlist apply before any action: `observation` excludes
`eval`, `interaction` adds click/typing/keyboard, and `privileged` covers
the sensitive capabilities (`eval`, cookies, storage, profiler, interception
and emulation). Target lifecycle stays exclusively with the supervisor.

**Secrets.** To keep a sensitive value out of argv, journals and proofs, use
`type --secret-env NAME`, `cookies set --value-env NAME`, `@env:NOM` in a
`record` action, and `secret_ref: NAME` in a scenario `type` step. These
references are resolved in memory and a missing reference is refused during
preflight, before any CDP effect.

## Features

The following eight sheets are the detailed user documentation:

| Feature | What it covers | Commands | Documentation |
|---|---|---|---|
| Navigation and synchronization | inspect the assigned target, open and wait for the useful state | `tabs`, `version`, `goto`, `wait` | [sheet](docs/features/browser-navigation.md) |
| DOM and user actions | read the rendering, act with trusted events | `eval`, `text`, `html`, `count`, `click`, `type`, `key` | [sheet](docs/features/dom-interaction.md) |
| Capture and observability | pixels, PDF, console, network, metrics | `screenshot`, `pdf`, `console`, `network`, `metrics` | [sheet](docs/features/browser-capture-observability.md) |
| State and session | isolated Chrome sessions, redacted cookies and storage | `session`, `cookies`, `storage` | [sheet](docs/features/state-session.md) |
| SEO, performance and accessibility | rendered DOM, vitals, AX tree, coverage | `seo`, `vitals`, `a11y`, `coverage` | [sheet](docs/features/seo-performance-accessibility.md) |
| Developer diagnostics | Symfony profiler and DOM diff | `profiler`, `dom-diff` | [sheet](docs/features/dev-profiler-diff.md) |
| Interception and orchestration | network mocking, emulation, scenarios, replay | `intercept`, `emulate`, `frame`, `record`, `replay`, `scenario` | [sheet](docs/features/orchestration-control.md) |
| Harness and proof | quality gates and validation report | `make` targets, `python -m cdpx.proof` | [sheet](docs/features/harness-proof-cockpit.md) |

### Index of the 31 commands

| Command | Role |
|---|---|
| `cdpx tabs` | inspect the single target assigned to the session |
| `cdpx version` | identify Chrome and the protocol version |
| `cdpx goto` | navigate and wait for a lifecycle event |
| `cdpx wait` | wait for a selector to appear |
| `cdpx eval` | run JavaScript in the page, as a last resort |
| `cdpx text` | read an element's text |
| `cdpx html` | read the rendered HTML |
| `cdpx count` | count the elements matching a selector |
| `cdpx click` | click through the Input domain |
| `cdpx type` | type text after a real focus |
| `cdpx key` | send a keystroke |
| `cdpx screenshot` | produce a PNG or JPEG capture |
| `cdpx pdf` | print the page to PDF |
| `cdpx console` | collect JavaScript logs and exceptions |
| `cdpx network` | capture the network activity of a navigation |
| `cdpx metrics` | read Chrome's Performance metrics |
| `cdpx cookies` | read, write or clear cookies |
| `cdpx storage` | inspect localStorage or sessionStorage |
| `cdpx seo` | extract the SEO contract of the rendered DOM |
| `cdpx vitals` | measure LCP, CLS and interaction signals |
| `cdpx a11y` | compact the accessibility tree |
| `cdpx coverage` | measure JavaScript and CSS coverage |
| `cdpx profiler` | read the Symfony profiler panels |
| `cdpx dom-diff` | compare the DOM before and after an action |
| `cdpx intercept` | continue, block or replace requests |
| `cdpx emulate` | apply a mobile, network or CPU profile |
| `cdpx frame` | read inside a same-origin iframe |
| `cdpx record` | run and journal an action as NDJSON |
| `cdpx replay` | replay a journal and detect divergences |
| `cdpx scenario` | run a YAML business scenario |
| `cdpx session` | create, inspect or stop a supervised browser session |

`cdpx --help` exposes the common options and `cdpx --version` the package
version. The detailed catalog and examples also live in
[docs/PRIMITIVES.md](docs/PRIMITIVES.md).

## Development and validation

```bash
make setup                 # editable install with the dev tools
make check-local           # ruff, format, mypy, unit tests
make check                 # full gate: Docker, Chrome and Symfony
make test-e2e              # real local Chrome; its absence is an error
make docker-symfony-e2e    # scenarios against the reference Symfony app
make proof                 # local report in .proof/
make release               # check + proof + verified wheel/sdist
```

Unit tests use a CDP mock that verifies both the output and the emitted
protocol. The E2E suites reuse the fixtures from `tests/fixtures/`. Docker,
Chrome and the Symfony suite are mandatory for a release verdict; they are
not silently skipped. The `.proof/` artifacts are generated locally and
private. CI publishes only `.proof/shareable/`, built from a manifest:
cleaned texts allowed, opaque files (captures, PDF, binaries) kept out of
staging. These build products are not sources to edit by hand.
The branch → PR → proof → review → merge cycle and the GitHub settings are
documented in [docs/GITHUB.md](docs/GITHUB.md).

Read [CONTRIBUTING.md](CONTRIBUTING.md) before a pull request and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for the participation rules.

## Documentation

- [HARNESS.md](HARNESS.md) — security, determinism and human supervision;
- [docs/CONTEXT.md](docs/CONTEXT.md) — motivations and technical decisions;
- [docs/PRIMITIVES.md](docs/PRIMITIVES.md) — complete catalog;
- [docs/SESSION-LIFECYCLE.md](docs/SESSION-LIFECYCLE.md) — Chrome launch,
  profiles, processes, lifecycle, teardown and session diagnostics;
- [docs/VALIDATION.md](docs/VALIDATION.md) — gates and proof matrix;
- [docs/GITHUB.md](docs/GITHUB.md) — PR cycle, checks, artifacts and
  governance;
- [docs/ROADMAP.md](docs/ROADMAP.md) and [docs/TODO.md](docs/TODO.md) —
  trajectory and remaining work;
- [docs/RELEASE-PLAN.md](docs/RELEASE-PLAN.md) — release preparation.

The cockpit generated by `make proof` also exposes a **Docs** tab: it renders
this curated catalog and the eight feature specifications from
`docs/features/` offline, Mermaid diagrams included.

## Help, contribution and security

- Usage questions and reproducible problems: [support
  policy](SUPPORT.md) then [GitHub issues](https://github.com/inem0o/cdpx/issues).
- Fixes and improvements: [contribution guide](CONTRIBUTING.md).
- Vulnerabilities: private reporting only through
  [the security policy](SECURITY.md), never in a public issue.

Community support is provided on a best-effort basis, with no guaranteed
response time.

## License

cdpx is distributed under the MIT license. See [LICENSE](LICENSE).
