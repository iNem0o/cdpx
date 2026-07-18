# Lessons-learned log

This log keeps reusable technical pitfalls without local paths or dependency
on a private tool. A session key is only a public Git reference used to
prevent duplicates.

- Session-Key: master@b647d66
  - Symptom: packaging tests passed on the host but failed in `docker-check`
    when they read the `.gitignore` and `.dockerignore` policies missing
    from the image.
  - Root cause (missing capability): the validation image did not copy all
    the policy files that its own tests consider harness inputs.
  - Fix encoded (doc/script/lint): the Dockerfile copies the public
    policies; `.dockerignore` excludes untracked workspaces and the
    packaging test locks in this reproduction.
  - Verification (command/CI): `make docker-check` then `make release`
    green.

- Session-Key: agent/github-integration-hardening@cdc4868
  - Symptom: `make check` was green on GitHub, then the Chrome relaunched by
    `make proof` announced DevTools but the 32 E2E tests timed out during
    `127.0.0.1` discovery.
  - Root cause (missing capability): loopback HTTP discovery inherited the
    runner's proxies and its 10-second readiness delay was too short to
    properly diagnose a loaded startup.
  - Fix encoded (doc/script/lint): loopback CDP calls use a direct urllib
    connection without a proxy, the delay stays bounded at 30 seconds and a
    test forces a dead proxy without breaking mock discovery.
  - Verification (command/CI): local `make release` green, then GitHub runs
    `29161949162` and `29162518918` green with `PR Gate / Required`.

- Session-Key: agent/github-integration-hardening@3547736
  - Symptom: `make proof` failed while the tests passed, because two
    record/replay test names no longer matched the proof globs; the first
    `make cov` pass also stayed under the threshold because of the
    supervisor.
  - Root cause (missing capability): the cockpit links proofs to pytest node
    IDs and the bootstrap/readiness/session-signal branches lacked
    deterministic coverage.
  - Fix encoded (doc/script/lint): the record/replay node IDs are realigned
    and bounded unit tests cover supervisor startup, errors, readiness and
    teardown without real Chrome.
  - Verification (command/CI): `make proof` green; `make cov` green at
    85.69%.

- Session-Key: agent/github-integration-hardening@0c4353d
  - Symptom: [HIGH] this cross-cutting standardization exceeded two hours
    without a tracked ExecPlan in the repository; the first release pass
    also interrupted an E2E session stop after 20 seconds while the CLI
    allows 30.
  - Root cause (missing capability): the repository provides neither
    `PLANS.md` nor a `docs/exec-plans/` directory, and the E2E wrapper's
    timeout was shorter than that of the contract it verifies.
  - Fix encoded (doc/script/lint): the E2E wrapper now waits 45 seconds and
    the supervised contract is locked in by the features and the cockpit;
    the absence of an ExecPlan remains to be addressed in a dedicated
    harness evolution.
  - Verification (command/CI): targeted session E2E test then `make release`
    green, cockpit at 551/551 tests with no violation or warning.

- Session-Key: agent/github-integration-hardening@7b7f4c0
  - Symptom: on GitHub, `make check` was green then the cold supervised
    Chrome relaunched by `make proof` timed out after 30 seconds; the
    teardown deleted `supervisor.log` and `chrome-stderr.log` before the
    gate could show them.
  - Root cause (missing capability): the parent and the supervisor shared
    the same timeout with no margin or global deadline, and Chrome used the
    CI runner's constrained `/dev/shm` without adaptation.
  - Fix encoded (doc/script/lint): the bootstrap has a dedicated bounded
    budget, a shared deadline with parent margin, `--disable-dev-shm-usage`
    in CI and private, bounded, redacted tails captured before the
    teardown.
  - Verification (command/CI): targeted unit tests, lifecycle E2E on real
    Chrome, local `make check-local` and `make release` green.

- Session-Key: agent/github-integration-hardening@336e519
  - Symptom: the cockpit displayed Mermaid sources without SVG in real
    Chrome; then `docker-check` failed while reading a third-party notice
    missing from the image.
  - Root cause (missing capability): a global escaping of `</` corrupted
    regular expressions in the minified bundle, and the Docker context did
    not yet reproduce all the packaging test's inputs.
  - Fix encoded (doc/script/lint): the inclusion checks the SHA-256 and only
    refuses a `</script` closing tag; the E2E requires four offline SVGs,
    and the Dockerfile as well as the packaging test lock in the
    third-party notice.
  - Verification (command/CI): targeted cockpit E2E, `make check`, `make
    proof` and `make dist` green.

- Session-Key: agent/github-integration-hardening@bc45078
  - Symptom: the generated report raised `Unexpected token ';'` because
    `cdpx-redacted` markers appeared in the middle of the minified Mermaid
    bundle.
  - Root cause (missing capability): the entire report, including static
    code, passed twice through detectors designed for free text; `data:` in
    a JavaScript property was mistaken for a Data URL.
  - Fix encoded (doc/script/lint): the dynamic summary is redacted before
    rendering, the pre-cleaned report crosses the staging without mutation
    and the Data URL detector now requires a compliant header; the canary
    scan remains final.
  - Verification (command/CI): redaction and staging tests, Chrome E2E on
    the shareable report, `make check`, `make proof`, `node --check` and
    `cmp` green.

## Cross CDP responses during an interception

- **Symptom:** `Page.navigate` timed out in Chrome Docker when Fetch
  suspended the document request before the navigation CDP response.
- **Cause:** the synchronous client lost command responses consumed while
  processing blocking events.
- **Durable fix:** `CDPClient.wait_response()` and a response buffer allow
  sending the navigation, processing `Fetch.requestPaused`, then retrieving
  the matching response.
- **Verification:** the real Chrome interception test and the `make check`,
  `make proof` and `make release` gates cover this path.

## Visible focus under headless Chrome

- **Symptom:** an RGAA check based solely on
  `getComputedStyle(...).outlineStyle` varied under headless Chrome.
- **Cause:** CSS intent was not a stable machine contract for the
  deterministic fixture.
- **Durable fix:** the reference Symfony app also exposes a
  `data-focus-visible` marker, checked by the E2E while keeping the real
  focus style.
- **Verification:** `make docker-symfony-e2e` then `make proof`.

Add an entry only when a runtime discrepancy produces generalizable
knowledge and a reproducible verification.
