# 08 — cdpx integration specification

Public routes are `rgaa catalog`, `rgaa scan`, `rgaa sample validate`, and
`rgaa sample run`. Catalog and sample validation are browser-free. Scan/run
reuse the supervised identity, lease, origin allowlist, timeout, redaction,
output bounding, and authority hierarchy.

Navigation, origin checks, native collectors, trusted input and optional
provider calls consume one global deadline. A failed direct navigation still
returns the complete 258-test result skeleton with explicit `error` verdicts;
sample execution applies the same rule per declared page and never drops a
later page from the aggregate.

Passive native collection requires observation. Trusted focus traversal
requires interaction. Text-spacing mutation and the isolated axe provider
require privileged authority. Samples calculate the maximum before CDP and
preflight all declared destinations. Redirected origins are checked after
every navigation and scan.
