# Leverage log

- Session-Key: master@944204e
  - Symptom: releasing 0.1.2 required rediscovering the whole procedure —
    the tag alone looked sufficient, while the version is actually pinned in
    thirteen files and the publication gate is the protected environment
    approval, not the tag.
  - Root cause (missing capability): the release order lived only in prose
    and the version fan-out had no mechanical guard, so a bump could silently
    miss a pinned surface.
  - Fix encoded (doc/script/lint): `docs/RELEASING.md` opens with a numbered
    runbook, and `test_release_version_pins_move_together` fails the unit
    gate naming any file whose pin lags the package version.
  - Verification (command/CI): `./dev check-local` and `./dev check` green on
    the 0.1.2 preparation commit; the test names the offending file when any
    single pin is reverted.

- Session-Key: master@3418047
  - Symptom: the first containerized proof runs could not start Chromium's
    sandbox, and the next unit run inherited a container marker in a test that
    intended to model a normal host user.
  - Root cause (missing capability): the proof environment allowlist omitted
    `CDPX_CONTAINERIZED`, while the sandbox unit contract did not isolate that
    variable. Newly added tooling tests were also absent from the proof
    cockpit's scenario mapping.
  - Fix encoded (doc/script/lint): the proof runner preserves the container
    marker; the sandbox test covers host, CI, root and container cases; and the
    harness feature sheet maps coverage, runtime configuration and OCI tooling
    tests to explicit scenarios.
  - Verification (command/CI): `./dev check` completed with 767 passing tests,
    real Chromium, real Symfony and a green proof inventory.

