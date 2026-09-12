# 07 — Recommended architecture

The accepted decision is canonical in
[`ADR 0003`](../architecture/decisions/0003-catalog-first-hybrid-rgaa.md).
Its enforceable boundaries are: offline pinned catalog, fixed probes,
preflighted authority, advisory isolated providers, seven conservative
verdicts, declared samples, and `certification_claim=false` at every
aggregation level.
