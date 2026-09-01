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

Initial document verification is also a reported collector boundary. A
deadline or transport failure after navigation but before `scan()` records
`initial-document-verification: error` in the complete JSON skeleton instead
of escaping through the generic CLI handler.

The internal document guard and command-level session guard are explicit final
phases. Deadline or transport failure at either boundary preserves stdout JSON
and evidence, records `final-document-verification: error`, invalidates
dependent automatic verdicts, and returns exit 1. Manual-only scans receive the
URL verified by the command after navigation.

`Page.navigate.errorText` is an operational navigation failure, not a usage
error: both direct scans and sample pages preserve it in their JSON report and
exit through the RGAA execution-status contract. Environment fingerprint
material is bounded in the page and hashed in Python, including on non-secure
HTTP origins. Passive and spacing work share node/byte budgets across nested
operations; the CDP execution timeout terminates only the isolated evaluation.
Focus restoration and key-up use a separate one-second cleanup budget and
recheck document identity and origin. A key-up is emitted only after dispatch
of rawKeyDown has begun. Execution status finalization is monotone
(`complete < partial < error`) and regenerates summary collector counts.

Environment collection is advisory. Browser metadata and page fingerprinting
have separate statuses; either failure makes execution partial without
overwriting normative verdicts. The bounded AX request reports domain
availability and explicitly reports that target correlation is absent.
Operational axe bundle, frame, isolated-world, result and parsing failures are
typed provider errors contained in the advisory provider status. Axe's promise
uses Chromium's renderer timeout as well as the transport timeout.

Passive native collection requires observation. Trusted focus traversal
requires interaction. Text-spacing mutation and the isolated axe provider
require privileged authority. Samples calculate the maximum before CDP and
preflight all declared destinations. Redirected origins are checked after
every navigation and scan.

Published execution plans count navigation and interaction separately. Sample
validation also publishes these totals and enabled collectors per page before
browser effects. A page reports page-local action use; a sample reports the cumulative total. Default,
small-limit and `--full` outputs all remain valid against the same public JSON
Schemas; normative arrays and top-level limitations are never shape-truncated.
