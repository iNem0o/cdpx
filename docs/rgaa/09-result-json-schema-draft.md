# 09 — Result schema

The executable contract is [`schemas/rgaa-result-v1.json`](../../schemas/rgaa-result-v1.json).
A page result carries catalog provenance, scope/engine/URL, full-count summary,
13 theme summaries, provider status, 258 test records, and global limitations.

Every test records its official ID, criterion/theme, verdict, automation
class, confidence, bounded findings/evidence/advisory observations and
limitations. Summary totals always cover the complete in-memory result.

Normative `themes`, `criteria`, `tests`, `pages`, `providers`, and
`limitations` arrays keep their published shape for every `--limit` value and
for `--full`. Variable evidence nested inside those records may still be
bounded. Page and sample documents expose `execution_status`,
`audit_findings_present`, and action use; sample page reports use page-local
counts while the sample top level carries the cumulative count. Page reports
separate browser/page-fingerprint environment status and publish bounded probe
metrics. Sample plans expose global and per-page action budgets and collectors;
sample results expose collector status for finalization errors.
