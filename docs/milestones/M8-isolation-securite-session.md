# M8 — Supervised sessions and trust boundary

## Why

An implicit first page and a shared Chrome become non-deterministic as soon
as several agents work in parallel. This milestone assigns a full browser
capability to each run, makes it the sole public contract, and closes the
trust, secrets and artifacts boundaries.

## Implemented contracts

### Assigned session

`cdpx session start` creates a disposable profile, a dynamic loopback port
and a single page target. The private manifest ties together `session_id`,
`run_id`, `target_id`, authority, origins, TTL, backend and supervisor. Every
browser command requires `--session`, `--run-id`, `--target`, explicitly or
via environment; the endpoint cannot be overridden and an exclusive lease
refuses concurrent commands.

Direct connection, the implicit choice of the first page and public target
lifecycle operations are removed. `tabs list` inspects only the attested
target. `make mock` creates the same manifest and the same supervised cycle
with a simulated backend.

`stop`, expiration, owner disappearance and supervised termination all go
through the teardown: target closed, Chrome terminated, profile and folder
deleted.

### Authorities and origins

- `observation`: navigation/reads/captures, never `eval`;
- `interaction`: observation + click/input/keyboard;
- `privileged`: eval, cookies, storage, profiler, interception, emulation and
  sensitive operations.

Composed commands are preflighted at the maximum level. A non-empty HTTP(S)
allowlist is mandatory; destination and actual origin are checked
before/after navigation and before mutation. Every output indicates
`_cdpx.content_trust: "untrusted"`.

### Secrets and proofs

CLI inputs, cookies, scenarios and journals use environment references.
Redaction covers known secrets, credentials, URL/query, headers, console,
network, profiler and errors. Private proofs are classified; only the
manifested text staging can be sent, after a canary scan.

### Interactions and orchestration

`wait_visible` checks actual visibility. `click` requires actionability and
a hit-test; `type --clear` selects then emits Backspace. Replay blocks
off-origin redirects and interception refuses unknown actions. Scenario
assertions come after the final drain.

## Targeted proofs present

- unit: policy, session, journal, redaction, artifacts, supervised CLI,
  scenarios and interactions;
- security integration: canaries in simulated stdout/stderr, URL, headers,
  console, storage, profiler, journal and artifacts, plus `0600`/`0700`
  modes;
- real Chrome: three simultaneous sessions prove isolated
  profiles/targets/states, authorities, lease and `stop`; a second scenario
  sends SIGTERM to the supervisor and proves profile deletion and port
  closure. Each scenario attaches a local screenshot classified
  `opaque-restricted` and a JSON.
- mock backend: a dedicated scenario proves private manifest, target
  attestation, command under identity triple and teardown without real
  Chrome.

The local `make test-e2e` target is green with these scenarios integrated.
The Symfony suite has its own separate, blocking Docker gate for the full
verdict.

## Integrated validation

The milestone is validated by `make check`, by the Chrome and mock session
scenarios collected in `make proof`, by `make cov` above the 85% threshold,
and by the isolated wheel installation that exposes the 31 expected
commands. The HARNESS, the feature sheets, the validation matrix and the
cockpit describe the same contract. The corresponding checkboxes are closed
in `docs/TODO.md`.

## Accepted limitations

- An abrupt machine shutdown can leave a private runtime folder to clean up.
- The TTL of local proofs is manifested but without a global purge daemon.
- Redaction does not guess every PII or unknown secret; opaque content
  remains unshareable by default.
