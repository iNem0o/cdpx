# 09 — Result schema

The executable contract is [`schemas/rgaa-result-v1.json`](../../schemas/rgaa-result-v1.json).
A page result carries catalog provenance, scope/engine/URL, full-count summary,
13 theme summaries, provider status, 258 test records, and global limitations.

Every test records its official ID, criterion/theme, verdict, automation
class, confidence, bounded findings/evidence/advisory observations and
limitations. Default output bounding may truncate the `tests` list but adds
`tests_total`, `tests_limit`, and `tests_truncated`; summary totals always
cover the complete in-memory result.
