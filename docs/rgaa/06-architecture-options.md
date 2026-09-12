# 06 — Architecture options

1. **Native-only:** smallest trust surface and exact control, but expensive to
   reproduce mature atomic observations.
2. **Provider-first:** quick coverage claims, but wrong semantics because
   WCAG/ACT IDs are not RGAA tests.
3. **Catalog-first hybrid:** cdpx owns catalog, applicability, evidence and
   verdicts; native rules prove modeled subsets; providers remain advisory.

Option 3 is selected. It preserves the complete unresolved inventory while
allowing incremental automation without adding Node as a runtime dependency.
