# M4 — SEO, performance, and accessibility measurement

## Why

Complementing the SEO contract of the rendered DOM with local signals of
performance, accessible semantics, and dead weight, without presenting them
as exhaustive audits.

## Delivered state

### `cdpx vitals`

LCP/CLS/INP `PerformanceObserver`s are injected before navigation. An
optional click produces a real interaction to try to feed INP. The values
are support-dependent and bounded by `--settle`: this local diagnostic is
neither a multi-run lab methodology nor CrUX/RUM field data.

### `cdpx a11y`

`Accessibility.getFullAXTree` produces a compact list of non-ignored nodes
`{role, name, ignored}`. It is a semantic view useful to the agent, not the
reproduction of a screen reader nor a complete RGAA audit. The RGAA checks
on the Symfony fixture constitute a separate automated subset.

### `cdpx coverage`

`Profiler.takePreciseCoverage` and `CSS.stopRuleUsageTracking` aggregate
used/unused JS bytes per resource and used/unused CSS rules. The measurement
only reflects the instrumented load: a feature that isn't exercised may
appear dead.

### `cdpx seo`

The diagnostic inspects title, metas, canonical, robots, h1, hreflang,
JSON-LD, images, and links of the rendered DOM. It stays on-page: no crawl,
indexing signal, backlink, server log, or Search Console.

## Proofs

The mock locks down the protocol and the outputs; real Chrome exercises the
signals on local fixtures; Symfony Docker adds the deterministic variants and
the RGAA subset. The assertions accept the absence of unsupported signals
instead of inventing cross-run stability.

## Definition of Done

- [x] vitals injected before navigation and optional interaction exercised;
- [x] compact AXTree documented and tested mock/Chrome;
- [x] JS/CSS coverage aggregated per resource and tested;
- [x] SEO/vitals/a11y limits made explicit in the user documentation.
