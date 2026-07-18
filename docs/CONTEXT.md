# CONTEXT.md — where this project comes from

## What already existed (starting point)

A first CLI tooling built on the Chrome DevTools Protocol gave an
agent the ability to **see and navigate** in a development Chrome,
in the context of Symfony applications, e-commerce sites
(Shopware/PrestaShop) and SEO operations.

Capabilities of the initial prototype:
- see the page,
- navigate,
- manage tabs,
- execute raw JS in the page.

## The request

1. **Find new usecases** offered by the CDP wiring already in place.
2. **Script new primitives** that improve the output of
   the agent AND of the dev driving it.
3. Deliver a **complete stack**: CLI for the agent, all the primitives,
   and above all a **deterministic test system** — a simple HTTP server +
   static HTML pages covering all the usecases.
4. Integrated agent harness, documentation of exchanges, complete todo list
   (what/how/why + original intent + examples), roadmap by
   milestones for what cannot be taken on right away.
5. Only put in place **what is 100% validatable at runtime** at the time
   of generation; document the rest.

## The guiding idea (from the discussion)

The existing wiring (see/navigate/tabs/raw JS) gives the agent
**hands**. What it lacks to produce better work are **senses** and
**measurement instruments**:

- **console + network**: an agent that reads neither the JS console nor
  failed requests debugs a frontend blind. These are the two feedback loops
  of frontend dev, and the most cost-effective primitives to add.
- **explicit wait** (`wait`): without it, the agent reads intermediate
  states (SPA, injected content) and draws false conclusions.
- **"trusted" interaction** (`click`/`type` via Input domain, not `el.click()`
  JS): reproduce what a real user produces, including for
  frameworks that filter `isTrusted`.
- **SEO audit of the rendered DOM** (`seo`): for e-commerce and SEO audits,
  what matters is the final DOM seen by Googlebot's rendering, not the served
  HTML. One primitive = one SEO contract extracted in a single call (title,
  metas, canonical, hreflang, JSON-LD, h1, alt, links).
- **measurement** (`metrics`, network weight): make things objective
  instead of just observing them.
- **state** (`cookies`/`storage`): understand and prepare scenarios
  (sessions, consent, cart) — with redaction by default, cf. HARNESS.md.

Heavier usecases identified but deferred to the roadmap (see ROADMAP.md):
reading the Symfony profiler via `x-debug-token-link`, request interception/
mocking (Fetch domain), before/after action DOM diff, device/network
emulation, the accessibility tree as a low-cost "semantic vision", session
record/replay, Core Web Vitals.

## Chrome execution constraint

Real Chrome e2e tests are now a blocking gate: `make test-e2e` and
`make proof` fail if no Chrome/Chromium binary is available in
the `PATH`. The tests start their own disposable headless profile and do
not attach to an already-open personal Chrome.

## Technical decisions and their reasons

| Decision | Reason |
|---|---|
| Python 3.11+, stdlib + targeted runtime dependencies (`websockets`, `markdown-it-py`, `PyYAML`) | `websockets` carries the synchronous CDP transport, `markdown-it-py` the CommonMark rendering of the proof cockpit and `PyYAML` the declarative scenarios; no application framework is introduced |
| **Sync** client (`websockets.sync`) | a CLI is sequential; no asyncio to propagate into the primitives or the tests |
| Direct connection to the page target's `webSocketDebuggerUrl` | simple model from the initial prototype; no flattened sessions to manage |
| CDP mock that **records commands** | test the protocol emitted, not just the output: a CDP params regression breaks a test, not a dev session |
| HTTP and WS discovery on two ports in the mock | simplicity; the client follows the URL published by /json, so full compatibility with real Chrome (a single port) |
| `/json/new` as PUT with GET fallback | Chrome ≥ 111 requires PUT; older headless versions accept GET |
| Cookies redacted by default | an agent copies its outputs verbatim; a session must not leak by accident |
| HTML fixtures with no external resource and no randomness | total determinism; the only delays are explicit (`/api/slow?ms=`, spa.html's 300ms setTimeout) |
| `Input.dispatch*` rather than `el.click()` | trusted events, real browser pipeline |
| CLI output = one JSON object | parsable by the agent, readable by humans, diffable in logs |
