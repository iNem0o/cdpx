# ADR 0001: Use one OCI graph and promote releases by digest

- Status: Accepted
- Date: 2026-07-19

## Context

Development, CI and release had accumulated separate host-Python, Docker and
package-distribution paths. Those paths could select different interpreters,
dependencies or browser versions, and a release rebuild could differ from the
candidate that passed the full gate.

## Decision

The pinned multi-stage `Dockerfile` is the common build graph for development,
CI, release and production:

- the host development interface is `./dev` and requires Docker only;
- the production unit is the multi-architecture OCI `runtime` image;
- the `cdpx` host launcher selects that image by digest and manages one
  hardened runtime per working tree;
- CI publishes per-architecture candidate images only after their gates pass;
- release combines and promotes the exact validated candidate manifest without
  rebuilding it;
- the Python wheel remains an internal image-build artifact;
- the relocatable embedded Linux archive is a compatibility path, not the
  normative runtime.

## Consequences

The Python and Chromium baselines are identical throughout the delivery
pipeline, and the promoted digest is traceable to the successful candidate
workflow. Docker becomes the required host dependency for the normative path.
Runtime image size and Docker-in-Docker behavior must remain explicit quality
concerns. Embedded compatibility needs its own glibc distribution matrix.

The rejected alternatives are a host-managed Python toolchain, public PyPI
distribution, and rebuilding mutable release tags. Each would reintroduce an
untested dependency or make the released bytes differ from the validated
candidate.

## Enforcement and evidence

- `tests/test_tooling_contract.py` checks the multi-stage graph, portable
  launchers, documentation matrix and digest-only promotion workflow.
- `./dev check` blocks on static checks, unit coverage, real Chromium, real
  Symfony and the proof inventory.
- `docs/RELEASE-ARCHITECTURE.md` and `docs/RELEASING.md` define the operating
  and recovery procedures.

