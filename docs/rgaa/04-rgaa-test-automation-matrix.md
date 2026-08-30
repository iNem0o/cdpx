# 04 — Automation matrix legend

The adjacent `03-rgaa-test-automation-matrix.csv` has exactly 258 data rows.
Each row declares detectability, automated applicability/pass/fail/NA powers,
human/AT/visual/interaction requirements, native/provider mappings, required
collectors/evidence, default unresolved verdict, confidence, limitations, and
rule version.

Classes are `deterministic`, `deterministic_partial`, `assisted`,
`interactive_assisted`, `emulated_assisted`, `review`, and `manual_only`.
“Partial” never means a partial pass: unmodeled branches stay
`needs_review`. Provider mappings state observation strength only and do not
delegate RGAA authority.
