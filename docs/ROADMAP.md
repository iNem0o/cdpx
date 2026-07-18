# Roadmap

Each technical milestone has a detailed sheet in
`docs/milestones/`. A milestone is declared complete only once its mock
and runtime proofs pass through the corresponding Make targets.

## M0 — CDP and CLI foundation ✅

Synchronous CDP client, `/json` discovery, CLI JSON/exit 0-1-2 contract,
scriptable mock, fixture server and first deterministic primitives.

## M1 — Real Chrome ✅

The primitives are exercised against Blink/V8 with a disposable profile and the
same fixtures as the unit tests. Chrome being absent is a gate failure,
not a degraded success. See [M1](milestones/M1-e2e-chrome.md).

## M2 — Symfony loop ✅

WebProfiler profiler, tracked console, DOM diff and scenarios against a real
Dockerized Symfony application. See [M2](milestones/M2-boucle-symfony.md).

## M3 — Interception and emulation ✅

Fetch continue/fulfill/block interception and mobile, network and CPU
profiles, validated in a persistent connection. See
[M3](milestones/M3-interception-emulation.md).

## M4 — SEO, performance and accessibility ✅

Vitals, accessibility tree, JS/CSS coverage and SEO audit enriched with the
rendered DOM. See [M4](milestones/M4-seo-perf.md).

## M5 — Orchestration and guardrails ✅

Record/replay, YAML scenarios, iframe, `CDPX_ORIGINS` and action budgets. See
[M5](milestones/M5-orchestration.md).

## M6 — Technical distribution ✅

Package version, wheel/sdist, `cdpx-ci` image, Symfony Compose and proof
cockpit. These capabilities are independent of the hosting platform. See
[M6](milestones/M6-distribution.md).

## M7 — Open source publication on GitHub 🚧

Goal: make the repository understandable, testable and publishable by an
outside person.

- MIT license and consistent public metadata;
- GitHub Actions as the primary CI, with Docker, Chrome and Symfony
  mandatory;
- contribution, security, support and GitHub templates;
- proof artifacts published by CI without being versioned;
- GitHub Release on tag and PyPI publication via Trusted Publishing;
- final validation on a GitHub runner before the first public tag.

Operational tracking lives in [TODO.md](TODO.md) and
[RELEASE-PLAN.md](RELEASE-PLAN.md).

## M8 — Supervised sessions and trust boundary ✅

Goal: make execution deterministic when several agents use
cdpx in parallel and make this supervision the product's single contract.

- Chrome session supervised per run: disposable profile, explicit target,
  exclusive lease, TTL/owner and teardown;
- `observation`, `interaction`, `privileged` levels, with preflighted
  composite commands and default refusal of unclassified capabilities;
- manifest/run/target identity and mandatory allowlist before discovery,
  before/after navigation check to close the redirection window;
- removal of direct connection, implicit target and public target
  lifecycle; mock backend exercised by the same supervised contract;
- journal v2 and secret references, cross-cutting redaction, classified
  private artifacts and allowlisted CI staging;
- hardened interactions (`wait_visible`, actionability, hit-test, event-based
  clear) and scenario assertions after final drain.

The code, targeted tests, the HARNESS, the feature sheets, the proof
cockpit, `make check`, `make proof` and validation of the distributed package
with 31 commands are green.
See [M8](milestones/M8-isolation-securite-session.md).
