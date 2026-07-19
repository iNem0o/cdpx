# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
cdpx uses semantic versioning.

## [0.1.0] — Unreleased

### Added

- 31 supervised Chrome DevTools Protocol commands covering navigation, DOM
  interaction, capture, observation, state, rendered-page audits, Symfony
  diagnostics and repeatable browser journeys.
- Disposable loopback Chrome sessions with an exact session/run/target
  identity, origin allowlists, authority levels, exclusive leases and bounded
  teardown.
- Deterministic CDP mock tests, real-Chrome scenarios, a Dockerized Symfony
  reference application and a private proof cockpit.
- Verified wheel and source distributions with Python 3.11 and 3.12 support.

### Security

- Cookie, storage and sensitive input values are redacted by default.
- Page, console, network and profiler content is marked as untrusted.
- Browser writes remain inside private session artifact directories; opaque
  files are excluded from automatic sharing.
