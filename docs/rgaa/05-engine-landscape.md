# 05 — Engine landscape

axe-core, IBM Equal Access, Lighthouse, Pa11y/HTML_CodeSniffer, QualWeb and ACT
rules provide useful WCAG/ACT observations but not RGAA test applicability,
French special cases, or exhaustive methodology. Asqatasun is the closest
automation benchmark yet still covers a subset; Ara is an expert audit
workflow rather than an automatic oracle.

cdpx therefore uses native CDP collectors for owned rules and axe-core only as
an optional offline advisory provider. Adding another provider requires a
version/hash/license pin, bounded projection, explicit mappings and evidence
that it cannot change capabilities or emit RGAA verdicts directly.
