# RGAA implementation pack

This directory records the research-to-runtime decisions behind `cdpx rgaa`.
Normative machine data lives under `src/cdpx/rgaa/data/4.1.2/`; public schemas
live under `schemas/`. The numbered documents preserve provenance, semantics,
security, validation, and the incremental maintenance path.

The generated CSV contains one row for every official test. Regenerate it
only with `python -m tools.generate_rgaa_catalog` after intentionally updating
the pinned sources and hashes.
