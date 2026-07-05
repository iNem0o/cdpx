+++
id = "orchestration-control"
title = "Interception, emulation and orchestration"
status = "validated"
summary = "Control network behavior, emulate device constraints, read frames and record/replay actions."
entrypoints = ["cdpx intercept", "cdpx emulate", "cdpx frame", "cdpx record", "cdpx replay"]
path_globs = ["src/cdpx/primitives/advanced.py", "src/cdpx/primitives/actions.py", "tests/fixtures/intercept.html", "tests/fixtures/iframe.html"]
test_globs = ["tests/test_primitives.py::test_intercept*", "tests/test_primitives.py::test_emulate*", "tests/test_primitives.py::test_frame*", "tests/test_primitives.py::test_record*", "tests/test_primitives.py::test_replay*", "tests/test_primitives.py::test_run_action*", "tests/test_primitives.py::test_origin_guard*", "tests/test_cli.py::test_record*", "tests/test_cli.py::test_replay*", "tests/test_cli.py::test_emulate*", "tests/e2e/test_e2e_chrome.py::test_intercept*", "tests/e2e/test_e2e_chrome.py::test_record_replay*", "tests/e2e/test_e2e_chrome.py::test_emulate*", "tests/e2e/test_e2e_chrome.py::test_origin_guard*"]
docs = ["docs/PRIMITIVES.md", "docs/milestones/M3-interception-emulation.md", "docs/milestones/M5-orchestration.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "intercept-network"
title = "Fulfill, block or continue matching network requests"
entrypoint = "cdpx intercept"

[[journeys]]
id = "replay-flow"
title = "Record and replay bounded browser actions"
entrypoint = "cdpx replay"

[[scenarios]]
id = "intercept-network-request"
journey = "intercept-network"
title = "Intercept a network request deterministically"
ui_text = "The browser run can force, block or continue network outcomes."
report_text = "This scenario proves that network behavior can be controlled during browser validation and linked to human-readable evidence."
given = "A fixture page performs requests that can be matched by interception rules."
when = "cdpx intercept applies fulfill, block or continue behavior."
then = "The browser result and screenshot prove the requested network path."
tests = ["tests/test_primitives.py::test_intercept*", "tests/e2e/test_e2e_chrome.py::test_intercept*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "orchestrate-replay-and-emulation"
journey = "replay-flow"
title = "Replay bounded browser orchestration"
ui_text = "The report links orchestration primitives to replay, frame, emulation and origin-guard tests."
report_text = "This scenario proves that bounded browser actions and device constraints can be replayed or inspected without becoming an unbounded macro language."
given = "Recorded actions, iframe fixtures or emulation constraints are available."
when = "cdpx replays, emulates, reads frames or enforces origin guard behavior."
then = "The result is bounded, reviewable and attached to the orchestration feature."
tests = ["tests/test_primitives.py::test_emulate*", "tests/test_primitives.py::test_frame*", "tests/test_primitives.py::test_record*", "tests/test_primitives.py::test_replay*", "tests/test_primitives.py::test_run_action*", "tests/test_primitives.py::test_origin_guard*", "tests/test_cli.py::test_record*", "tests/test_cli.py::test_replay*", "tests/test_cli.py::test_emulate*", "tests/e2e/test_e2e_chrome.py::test_record_replay*", "tests/e2e/test_e2e_chrome.py::test_emulate*", "tests/e2e/test_e2e_chrome.py::test_origin_guard*"]
expected_proofs = ["junit", "screenshot"]
+++

## Intent

Support controlled browser experiments where network, device conditions or
multi-step action logs are part of the validation.

## User journeys

- Intercept a navigation and force deterministic network outcomes.
- Emulate mobile, slow network or CPU slowdown.
- Read iframe text and replay recorded actions with a budget.

## Validation

Unit tests validate rules and replay divergence; e2e validates real Fetch
interception.

## Evidence

Expected evidence is JUnit and screenshots for e2e orchestration scenarios.

## Known gaps

Record/replay executes real actions but the action language stays
intentionally compact (goto, wait, click, type, key, eval) — it is not a full
browser macro language.
