# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
cdpx uses semantic versioning.

## [0.1.0] — 2026-07-19

### Added

- 31 supervised Chrome DevTools Protocol commands covering navigation, DOM
  interaction, capture, observation, state, rendered-page audits, Symfony
  diagnostics and repeatable browser journeys.
- Disposable loopback Chrome sessions with an exact session/run/target
  identity, origin allowlists, authority levels, exclusive leases and bounded
  teardown.
- Deterministic CDP mock tests, real-Chrome scenarios, a Dockerized Symfony
  reference application and a private proof cockpit.
- One pinned, multi-stage OCI toolchain for development, validation, release
  and the production runtime, exposed locally through the Docker-only `./dev`
  portal.
- A digest-pinned `cdpx` host launcher that manages one hardened runtime per
  working tree, validates `cdpx.yaml`, and exposes runtime lifecycle commands.
- Multi-architecture OCI releases for amd64 and arm64, plus an optional
  relocatable embedded Linux artifact for environments that cannot run
  containers.
- Normative user and integrator documentation for installation, configuration,
  development, runtime integration, release architecture and troubleshooting.

### Changed

- The public distribution is now the signed-off OCI image promoted by digest;
  PyPI wheels and source archives are internal build evidence only.
- Session manifests use `cdpx.session/v2` and attest the runtime identity.
- Python 3.14 is the single interpreter baseline across local development, CI
  and release.

### Security

- Cookie, storage and sensitive input values are redacted by default.
- Page, console, network and profiler content is marked as untrusted.
- Browser writes remain inside private session artifact directories; opaque
  files are excluded from automatic sharing.
- The production runtime is read-only, capability-free, protected by
  `no-new-privileges`, and receives configuration through a mode-0600
  environment file rather than command-line secrets.
