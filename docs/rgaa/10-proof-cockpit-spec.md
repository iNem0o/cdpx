# 10 — Proof cockpit specification

RGAA evidence uses the existing feature/journey/scenario proof inventory.
The `rgaa-audit` feature attaches JUnit plus bounded JSON for the baseline and
controlled regression. Test results already carry the hierarchy needed for a
future dedicated drill-down: theme → criterion → test → finding/evidence.

Any cockpit extension must prioritize fail/error/unresolved backlogs over a
score, show source/catalog hashes, retain `not_tested`, and never display a
certification badge. Screenshots or full HTML remain opaque-restricted and are
not produced by the default scan.
