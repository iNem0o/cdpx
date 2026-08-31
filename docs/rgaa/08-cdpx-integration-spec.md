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

`Page.navigate.errorText` is an operational navigation failure, not a usage
error: both direct scans and sample pages preserve it in their JSON report and
exit through the RGAA execution-status contract. Environment fingerprint
material is bounded in the page and hashed in Python, including on non-secure
HTTP origins. An isolated probe timeout never terminates target-wide JavaScript.

Passive native collection requires observation. Trusted focus traversal
requires interaction. Text-spacing mutation and the isolated axe provider
require privileged authority. Samples calculate the maximum before CDP and
preflight all declared destinations. Redirected origins are checked after
every navigation and scan.

Published execution plans count navigation and interaction separately. A page
reports page-local action use; a sample reports the cumulative total. Default,
small-limit and `--full` outputs all remain valid against the same public JSON
Schemas; normative arrays and top-level limitations are never shape-truncated.
