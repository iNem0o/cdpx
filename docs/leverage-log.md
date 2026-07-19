# Leverage log

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

