# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
cdpx uses semantic versioning.

## [0.1.4] — 2026-07-23

### Added

- `runtime.trust_ca` in `cdpx.yaml` lists workspace CA certificates (PEM),
  bind-mounted read-only and imported into a per-session trust store at
  `session start`, so a supervised Chrome trusts a local development
  authority (`mkcert`, traefik) instead of failing with
  `ERR_CERT_AUTHORITY_INVALID`. Copy only `rootCA.pem`; a file containing a
  `PRIVATE KEY` block is rejected at compilation. The runtime image now
  bundles `certutil` (via `libnss3-tools`) to perform the import.
- `session.ignore_tls_errors` in `cdpx.yaml` and the matching
  `cdpx session start --ignore-tls-errors` flag launch Chrome with
  `--ignore-certificate-errors`, a dev-only fallback for local HTTPS behind
  an untrusted development CA.

### Changed

- **Breaking**: session manifests move from `cdpx.session/v2` to
  `cdpx.session/v3`. An active session created by an older version fails
  closed; clear it with `cdpx runtime reset --force`.

## [0.1.3] — 2026-07-22

### Added

- `runtime.extra_hosts` in `cdpx.yaml` maps hostnames to an IP address or
  to `host-gateway` (`--add-host`), so a runtime joined to a development
  stack network resolves names the stack only registers in the host's
  `/etc/hosts`.
- Environment interpolation in `cdpx.yaml` values: `${NAME}`,
  `${NAME:-default}` and `$$` resolve against the calling environment at
  plan compilation, letting stack tooling drive the network name and
  extra hosts through exported variables.

### Changed

- **Breaking**: `$` is now reserved in every `cdpx.yaml` string value.
  A literal `$` accepted by earlier releases must be escaped as `$$`;
  any other bare `$` fails compilation with a `malformed placeholder`
  error.

### Documentation

- The README, homepage and installation guide now separate installed mode
  (launcher deployment, updates, constraints, uninstall) from dev mode
  (contributing through `./dev`), and a "How cdpx runs" section
  disambiguates the installed launcher, the in-image CLI and the
  contributor harness. The homepage version badge is corrected and now
  covered by the release version-pin test.

## [0.1.2] — 2026-07-21

### Added

- A public agent-assisted onboarding guide and reusable `cdpx` skill for safe
  project setup, supervised browser use and troubleshooting.

## [0.1.1] — 2026-07-19

### Fixed

- The released launcher refused to run: the release digest substitution
  also rewrote the unreleased-guard pattern, so every published launcher
  matched its own digest. The substitution is now anchored to the
  `DEFAULT_IMAGE` line and the launcher test bakes a digest exactly as
  the release workflow does.

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
