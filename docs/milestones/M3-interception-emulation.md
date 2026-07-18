# M3 — Interception and emulation

## Why

Testing conditions encountered in production without modifying the backend: an
unavailable API, a blocked third party, mobile viewport, slow network, or
constrained CPU.

## Delivered state

### `cdpx intercept`

`Fetch.enable` suspends the requests of the composed navigation. The first
rule whose URL pattern matches applies exactly one of the following actions:

- `continue` → `Fetch.continueRequest`;
- `block` → `Fetch.failRequest(BlockedByClient)`;
- status `200..599` → `Fetch.fulfillRequest` with a mock JSON body.

The matcher operates on the URL (fnmatch or substring), not on the HTTP
method. A typo or an out-of-range status is rejected at parsing before any
CDP effect. Interception only composes with `goto` because the Fetch state
dies with the connection:

```bash
cdpx intercept --rule "*api/payment* => 503" --rule "*googletagmanager* => block" -- goto http://shop.test/checkout
```

The dedicated `intercept.html` fixture, the mock, and real Chrome cover
continue, fulfill, and block. The managed sessions from M8 isolate runs, but
do not make interception persistent across commands.

### `cdpx emulate`

The `mobile`, `slow-3g`, and `cpu-4x` presets configure metrics/UA, network
conditions, or CPU throttling. `--reset` restores metrics, user-agent, network,
and CPU. Since overrides die with the connection, the useful action happens
in the same invocation:

```bash
cdpx emulate mobile -- goto http://shop.test/checkout
```

## Proofs and limits

- The mock locks down the methods/parameters and the rejection of invalid
  rules.
- Real Chrome verifies interception, viewport/UA, reset, latency, and
  screenshot.
- The proof does not claim an exhaustive desktop/mobile pixel comparison.
- `intercept` does not yet wrap a click or an entire scenario.

## Definition of Done

- [x] fulfill/fail/continue rules tested with mock and real Chrome;
- [x] unknown actions rejected before navigation;
- [x] presets/reset documented and exercised on real Chrome;
- [x] connection/interception lifetime made explicit.
