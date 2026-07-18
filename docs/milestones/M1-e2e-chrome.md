# M1 — Real Chrome E2E

## Why

The mock validates the emitted CDP protocol, not Blink/V8 behavior: rendering,
event timing, trusted input, download, or capture dimensions.
The real suite therefore complements the mock without replacing it.

## Validated state

`tests/e2e/test_e2e_chrome.py` exercises the command families against the same
deterministic fixtures as the unit tests; `test_e2e_sessions.py` covers
the multi-session lifecycle. They notably cover:

- navigation, SPA wait, and tab lifecycle;
- click, input, keyboard, iframe, and origin guard;
- PNG/JPEG/PDF capture, console, network, and metrics;
- cookies, storage, SEO, vitals, accessibility, and coverage;
- interception, emulation, record/replay, and declarative scenarios;
- contract of the installed binary: stdout, stderr, and exit codes.
- three simultaneous profiles/targets, cookie/storage isolation, grants, lease,
  stop, and teardown on normal supervisor signal.

Every scenario that requires visual proof attaches a local `opaque-restricted`
screenshot to the case folder. The manifest links it to the proof, but
its bytes do not automatically leave `.proof/` via CI staging.

## Execution

```bash
make test-e2e
make docker-e2e
```

The local run uses a disposable Chrome profile and the debug port on
loopback. The absence of Chrome or a skip turns the gate red. In GitHub CI,
the Docker target reproduces the browser environment.

## Invariants

- a Chrome/mock divergence triggers a test and, if necessary, a mock
  update;
- no external network access from the fixtures;
- no unbounded sleep;
- no connection to the user's personal Chrome;
- generated artifacts stay in `.proof/` or in CI artifacts.

## Definition of Done

- [x] real Chrome suite green locally and in the Docker image;
- [x] absence of Chrome treated as an error;
- [x] screenshots and JUnit attached to the proof cockpit;
- [x] black-box scenarios for the installable binary.
