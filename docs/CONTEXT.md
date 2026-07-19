# Product rationale and design

cdpx gives agents and developers a narrow, scriptable interface to a
development Chrome. Browser automation is useful only when the caller can
identify the exact page, bound every action, inspect what happened and leave
reproducible evidence. The CLI therefore favors focused primitives over a
general remote-control layer.

## Product needs

cdpx covers six complementary needs:

1. **See** the rendered DOM, console and network activity.
2. **Measure** browser metrics and Symfony profiler data.
3. **Audit** rendered SEO, accessibility and code coverage.
4. **Reproduce** state, network conditions and user journeys.
5. **Prove** behavior with captures, scenarios and a validation cockpit.
6. **Lock down** the browser through supervised sessions and explicit policy.

Navigation, synchronization and trusted input support every family.

## Design choices

- **Synchronous CLI:** one invocation maps to one bounded action and one JSON
  result. Shell scripts, agents and humans share the same interface.
- **Supervised page target:** the session owns a disposable Chrome profile,
  loopback endpoint and one assigned page. The session/run/target identity is
  mandatory for browser commands.
- **Direct page WebSocket:** commands connect to the assigned page target
  without an extra multiplexing layer.
- **Protocol-faithful mock:** deterministic tests assert returned data and the
  exact CDP methods, parameters and order.
- **Real runtime gates:** Chrome and the Dockerized Symfony application verify
  browser engine and framework behavior that the mock cannot provide.
- **Safe defaults:** origin checks fail closed, sensitive values are redacted,
  outputs are bounded and page-derived content is untrusted.
- **Private evidence:** screenshots, PDFs, logs and reports remain in managed
  artifact directories. Only a cleaned manifest allowlist can become
  shareable.

Chrome discovery uses `PUT /json/new`; an explicit method rejection permits a
bounded `GET` fallback for compatible endpoints. Cookie clearing similarly
uses `Storage.clearCookies` with the browser-supported Network-domain
fallback. These are current compatibility behaviors and are covered by
protocol tests.

## Intended environment

cdpx targets disposable development browsers, local or controlled reference
applications, Symfony diagnostics, e-commerce journeys and rendered-page
audits. It is not a way to attach to a personal Chrome profile, bypass browser
security or certify an entire production system.

The normative execution boundary is [HARNESS.md](../HARNESS.md). The command
surface is [PRIMITIVES.md](PRIMITIVES.md), the lifecycle is
[SESSION-LIFECYCLE.md](SESSION-LIFECYCLE.md), and release evidence is defined
in [VALIDATION.md](VALIDATION.md).
