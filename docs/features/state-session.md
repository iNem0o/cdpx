+++
id = "state-session"
title = "Session state and controls"
status = "validated"
summary = "Assign a supervised browser session and inspect cookies/localStorage/sessionStorage without leaking secrets by default."
entrypoints = ["cdpx cookies", "cdpx storage", "cdpx session"]
path_globs = ["src/cdpx/session.py", "src/cdpx/policy.py", "src/cdpx/artifacts.py", "src/cdpx/security/*.py", "src/cdpx/primitives/state.py", "src/cdpx/testing/mock_session.py", "tests/test_session.py", "tests/test_policy.py", "tests/test_session_cli.py", "tests/test_artifacts.py", "tests/test_redaction.py", "tests/test_security_integration.py", "tests/e2e/test_e2e_sessions.py", "tests/fixtures/storage.html", "src/cdpx/sessions/*.py", "src/cdpx/private_files.py", "tests/test_private_files.py"]
test_globs = ["tests/test_cli.py::test_cookies*", "tests/test_cli.py::test_missing_session*", "tests/test_cli.py::test_direct_connection_options*", "tests/test_primitives.py::test_cookies*", "tests/test_primitives.py::test_set_and_clear*", "tests/test_primitives.py::test_clear_cookies*", "tests/test_primitives.py::test_get_storage*", "tests/test_primitives.py::test_console_entries_redact*", "tests/test_session.py::*", "tests/test_policy.py::*", "tests/test_session_cli.py::*", "tests/test_artifacts.py::*", "tests/test_redaction.py::*", "tests/test_security_integration.py::*", "tests/test_scenarios.py::test_scenario_secret_ref_never_reaches_outputs_or_evidence", "tests/e2e/test_e2e_chrome.py::test_cookies*", "tests/e2e/test_e2e_chrome.py::test_cli_cookie_masking*", "tests/e2e/test_e2e_sessions.py::*", "tests/test_private_files.py::*", "tests/test_primitives.py::test_storage_rejects_unknown_kind*"]
docs = ["docs/PRIMITIVES.md", "docs/SESSION-LIFECYCLE.md", "HARNESS.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "read-session"
title = "Inspect the browser's session state"
entrypoint = "cdpx cookies"

[[journeys]]
id = "prepare-session"
title = "Set or clear cookies for a repeatable scenario"
entrypoint = "cdpx cookies"

[[journeys]]
id = "isolate-session-runs"
title = "Assign a disposable, exclusive browser to each run"
entrypoint = "cdpx session"

[[journeys]]
id = "exercise-session-without-chrome"
title = "Exercise the supervised contract with the mock backend"
entrypoint = "cdpx session"

[[journeys]]
id = "teardown-supervisor-signal"
title = "Destroy the browser and profile when the supervisor stops"
entrypoint = "cdpx session"

[[scenarios]]
id = "read-session-state"
journey = "read-session"
title = "Safely read the browser's session state"
ui_text = "The user can inspect cookies and storage without exposing secret values by default."
report_text = "This scenario proves that the browser's session state is observable while keeping cookie and storage values redacted, unless explicitly requested."
given = "A local storage fixture sets cookies and browser storage values."
when = "cdpx reads the cookies, localStorage, or sessionStorage."
then = "The output is structured and safe to review in the proof report."
tests = ["tests/test_cli.py::test_cookies*", "tests/test_primitives.py::test_cookies*", "tests/test_primitives.py::test_get_storage*", "tests/e2e/test_e2e_chrome.py::test_cookies*", "tests/test_primitives.py::test_storage_rejects_unknown_kind*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "prepare-repeatable-session"
journey = "prepare-session"
title = "Prepare a repeatable browser session state"
ui_text = "The agent can set or clear the session state before running a scenario."
report_text = "This scenario proves that repeatable browser workflows can prepare cookies before the action, while keeping the same review traceability."
given = "A browser target accepts cookie mutation via CDP."
when = "cdpx sets or clears the cookies for the target origin."
then = "The following steps run on a controlled session state."
tests = ["tests/test_primitives.py::test_set_and_clear*", "tests/test_primitives.py::test_clear_cookies*"]
expected_proofs = ["junit"]

[[scenarios]]
id = "isolate-supervised-session-runs"
journey = "isolate-session-runs"
title = "Isolate and destroy supervised browser sessions"
ui_text = "Each run receives a distinct profile, target, authority, and loopback endpoint, usable by only one command at a time."
report_text = "This scenario proves on real Chrome the isolation of three runs, the absence of cookie/storage sharing, the authority matrix, the exclusive lease, and the teardown of profiles/endpoints."
given = "Three runs start supervised sessions with the observation, interaction, and privileged authorities."
when = "The CLI performs reads, interactions, and privileged operations, attempts a concurrent lease, then stops each session."
then = "The browser states remain isolated, the authorities are enforced, the second lease fails, and each profile/endpoint disappears at teardown."
tests = ["tests/test_cli.py::test_missing_session*", "tests/test_cli.py::test_direct_connection_options*", "tests/test_session.py::*", "tests/test_policy.py::*", "tests/test_session_cli.py::*", "tests/e2e/test_e2e_sessions.py::test_supervised_sessions_are_isolated_authorized_and_torn_down", "tests/test_private_files.py::*"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "run-supervised-mock-session"
journey = "exercise-session-without-chrome"
title = "Use the mock backend through a supervised session"
ui_text = "The developer can start a session in the foreground without Chrome and use the same identity triple as everywhere else."
report_text = "This scenario proves that the mock creates a private manifest, attests a target, and applies the same session/run/target contract before deleting everything."
given = "Real Chrome is not required and the mock CDP backend is available on loopback."
when = "A supervised mock session starts, runs a command, then stops."
then = "The command goes through the assigned manifest and the teardown deletes the private resources."
tests = ["tests/test_session.py::test_mock_backend_uses_supervised_session_contract"]
expected_proofs = ["junit"]

[[scenarios]]
id = "mark-page-content-untrusted"
journey = "read-session"
title = "Mark page content as untrusted"
ui_text = "Any data read from a page comes back labeled untrusted, never as an instruction to follow."
report_text = "This scenario proves that a read under the observation authority stays confined to the allowed origin and that the output carries content_trust=untrusted, even when the page tries to inject an instruction to the harness."
given = "A page served on the allowed origin returns text that mimics an injection instruction."
when = "cdpx reads the page text under the observation authority in a supervised session."
then = "The text is returned as data accompanied by the _cdpx content_trust=untrusted block, without ever being executed."
tests = ["tests/test_session_cli.py::test_session_observation_is_scoped_and_emits_untrusted_metadata"]
expected_proofs = ["junit", "command"]

[[scenarios]]
id = "redact-sensitive-session-data"
journey = "read-session"
title = "Prevent a canary from leaving the secured run"
ui_text = "Cookies, storage, URL, headers, console, profiler, log, and artifacts are cleaned before sharing."
report_text = "This scenario proves that known canaries are absent from outputs and artifacts, that ordinary text stays readable, and that private permissions are enforced."
given = "The mock CDP exposes a canary secret across several browser surfaces."
when = "cdpx observes, logs, and builds a shareable staging area."
then = "The protocol may receive the value in memory, but stdout, stderr, log, and shareable artifacts do not contain it."
tests = ["tests/test_cli.py::test_cookies_masked_output", "tests/test_primitives.py::test_console_entries_redact*", "tests/test_artifacts.py::*", "tests/test_redaction.py::*", "tests/test_security_integration.py::*", "tests/test_scenarios.py::test_scenario_secret_ref_never_reaches_outputs_or_evidence"]
expected_proofs = ["junit", "json"]

[[scenarios]]
id = "teardown-on-supervisor-signal"
journey = "teardown-supervisor-signal"
title = "Clean up a session after a SIGTERM from the supervisor"
ui_text = "A supervised shutdown closes the browser, the CDP port, and deletes the private profile without requiring a second command."
report_text = "This scenario proves on real Chrome that the supervisor's teardown block also runs on a normal SIGTERM."
given = "A supervised session is active with a target and a disposable profile."
when = "The supervisor process receives SIGTERM."
then = "The manifest, profile, and folder disappear, and the loopback port no longer accepts connections."
tests = ["tests/e2e/test_e2e_sessions.py::test_supervisor_signal_still_tears_down_chrome_and_private_files"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "supervise-lifecycle-without-chrome"
journey = "exercise-session-without-chrome"
title = "Run the full supervised lifecycle without real Chrome"
ui_text = "The supervisor attests the bootstrap, publishes the manifest, closes surplus targets, then destroys the session at shutdown, even with a simulated browser."
report_text = "This scenario proves, without real Chrome, that the supervisor rejects an invalid attestation with no side effect, writes a reloadable manifest pointing at the assigned target and discovered port, closes the initial tab and then the target at teardown without touching the worker, terminates and then kills an unresponsive browser, and deletes the session exactly once on SIGTERM."
given = "An attested bootstrap describes a session whose Chrome and HTTP discovery are simulated."
when = "The supervisor first receives an invalid attestation, then the correct attestation, and runs through to SIGTERM."
then = "The invalid attestation fails without touching any files, the published manifest is reloadable, the surplus targets are closed, and the session is destroyed only once."
tests = ["tests/test_session.py::test_supervisor_builds_manifest_closes_extra_target_and_cleans_up"]
expected_proofs = ["junit"]

[[scenarios]]
id = "report-redacted-startup-diagnostics"
journey = "exercise-session-without-chrome"
title = "Report redacted startup diagnostics before cleanup"
ui_text = "When the session is not ready in time, the tail of the supervisor and Chrome logs comes back in the error, with secret values redacted, before the private runtime disappears."
report_text = "This scenario proves that a startup that times out names both logs and the readiness step reached, redacts the secret value coming from the environment (*** instead of the token), and only cleans up after reading the tails."
given = "A simulated supervisor stalls at the wait_devtools stage while writing a secret into its logs."
when = "start_session exceeds its startup budget."
then = "The PolicyError cites supervisor.log and chrome-stderr.log, the secret is replaced with ***, and then the private session is deleted."
tests = ["tests/test_session.py::test_start_session_timeout_reports_redacted_log_tails_before_cleanup"]
expected_proofs = ["junit", "logs"]

[[scenarios]]
id = "public-manifest-hides-control-levers"
journey = "isolate-session-runs"
title = "Restrict the public manifest view to the logical identity"
ui_text = "The public output of session start reveals the run and target identity but never the websocket endpoint, the profile path, or the browser PID."
report_text = "This scenario proves that public_dict exposes run_id, target_id, and the ephemeral profile while omitting websocket_url, profile_dir, and browser_pid — the browser takeover levers stay private (invariant 5)."
given = "A complete supervised session manifest is built."
when = "The caller reads the public view of the manifest."
then = "The logical identity is present and no browser or profile attack capability leaks into the default output."
tests = ["tests/test_session.py::test_public_manifest_omits_capabilities_and_physical_profile"]
expected_proofs = ["junit", "json"]
+++

## Intent

Assign each run a disposable, exclusive, supervised browser session, then
enable repeatable scenarios without accidental leaks. This contract is
the sole entry point for browser commands, with real Chrome as with the
mock backend. Cookies and storage are redacted by default; showing them
is a deliberate, privileged act.

## Usage

Global options and exit codes: see the CLI Contract section of the README.

### `cdpx session`

```text
usage: cdpx session start --run-id RUN --authority observation|interaction|privileged --origins ORIGINS [--ttl S] [--startup-timeout S] [--export]
usage: cdpx session status --session PATH --run-id RUN --target ID
usage: cdpx session stop --session PATH --run-id RUN --target ID
```

`start` launches a headless Chrome on loopback with a dynamic port, a
disposable profile, a single target, and a supervisor. The private
manifest associates these resources with the run, the authority, and the
allowlist. The public output omits the PIDs, the profile/artifact paths,
and the WebSocket URL; it provides the manifest path and the identity
needed by the commands.

The binary selection, the exact command line, the process tree, the
private files, the exposed surfaces, and every teardown path are
documented in [Supervised sessions and Chrome processes](../SESSION-LIFECYCLE.md).
Cold start defaults to 60 seconds, within a strict 300-second limit. The
parent waits out this budget plus a short handoff margin, without racing
the supervisor's internal timeout. On a CI runner, Chrome avoids the
often-bounded `/dev/shm`. If startup fails, the `supervisor.log` and
`chrome-stderr.log` tails are bounded, redacted, and then surfaced in the
diagnostic before teardown; the raw private files are still deleted.

```bash
cdpx session start --run-id checkout-17 --authority interaction --origins "http://*.test,http://127.0.0.1:*" --ttl 1800
cdpx session status --session /tmp/cdpx-session/manifest.json --run-id checkout-17 --target ABC123
cdpx session stop --session /tmp/cdpx-session/manifest.json --run-id checkout-17 --target ABC123
```

The three identifiers can be exported at once. `--export` replaces the
startup JSON with the three quoted `export` lines (`CDPX_SESSION`,
`CDPX_RUN_ID`, `CDPX_TARGET`) — this is the documented exception to the
stdout-JSON contract, meant for `eval`, `ssh-agent`-style:

```bash
eval "$(cdpx session start --run-id checkout-17 --authority interaction --origins "http://*.test" --export)"
cdpx text "#cart"
```

Manual exports from the JSON output are still possible:

```bash
export CDPX_SESSION=/tmp/cdpx-session/manifest.json
export CDPX_RUN_ID=checkout-17
export CDPX_TARGET=ABC123
cdpx text "#cart"
```

The manifest is `0600`, its folder/profile/artifacts are private, and a
non-blocking lease prevents two commands from driving the target at the
same time. The supervisor destroys the session on `stop`, TTL, or the
disappearance of its runtime guardian. The endpoint comes only from the
manifest, the allowlist cannot be empty, and every output carries
`_cdpx.content_trust: "untrusted"`.

`./dev mock` runs the same cycle in the foreground with a simulated
browser, prints the three exports, and cleans up the session on
`Ctrl-C`.

### `cdpx cookies`

```text
usage: cdpx cookies {get,set,clear} [--show-values] [--name NAME] [--value-env NAME] [--url URL]
```

Reads, sets, or clears the cookies of the disposable profile. `get`
redacts all values by default. `set` requires `--name`, `--value-env`,
and `--url`; the value is never accepted literally on the command line.

```bash
cdpx cookies get
cdpx cookies get --show-values
cdpx cookies set --name PHPSESSID --value-env CHECKOUT_SESSION --url http://demo.test/
cdpx cookies clear
```

Read output:

```json
{"cookies": [{"name": "PHPSESSID", "value": "***", "domain": "demo.test", "path": "/"}], "count": 1, "values_masked": true, "_cdpx": {"content_trust": "untrusted"}}
```

`--show-values` is a deliberate elevation: its output does not belong in
a commit, a ticket, or a proof. Cross-cutting redaction still takes
priority and therefore re-masks a secret that was already recorded, even
with this option. `clear` purges the entire assigned profile. A fallback
to `Network.clearBrowserCookies` maintains compatibility with Chrome
versions that do not yet offer `Storage.clearCookies`.

### `cdpx storage`

```text
usage: cdpx storage [--kind {local,session}] [--show-values]
```

Reads the `localStorage` or `sessionStorage` of the current page. Strings
are redacted by default, with no attempt to distinguish a cart value
from a token.

```bash
cdpx storage
cdpx storage --kind session
```

```json
{"kind": "session", "entries": {"cart": "***", "consent": "***"}, "count": 2, "values_masked": true, "_cdpx": {"content_trust": "untrusted"}}
```

## User journeys

- Start a session, export its identity, inspect it, then stop it.
- Exercise this exact cycle without real Chrome using `./dev mock`.
- Read cookies and storage with values redacted by default.
- Set a cookie from an environment reference, or purge the profile.

## Validation

The mock unit tests enforce the identity triple, metadata, redaction,
secret references, allowlist, authority matrix, private manifests,
lease, and artifact confinement. The multi-session e2e launches three
Chrome instances and checks distinct profiles/targets, state isolation,
authorities, and teardown. A dedicated unit scenario proves that the
mock backend takes the same supervised path.

## Proofs

Expected proofs: JUnit, isolation/teardown JSON, and local
`opaque-restricted` screenshots for the real Chrome scenarios; JUnit for
the mock cycle.

## Known limitations

- `--show-values` deliberately bypasses redaction and must not be
  persisted.
- `cookies clear` is global to the assigned disposable profile, with no
  origin-targeted purge.
- The supervisor covers managed terminations; after an abrupt machine
  shutdown, the private runtime directory may need cleanup on restart.
