"""Offline, integrity-checked access to the official RGAA 4.1.2 snapshot."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

RGAA_VERSION = "4.1.2"
CATALOG_ID = "rgaa-4.1.2"
EXPECTED_COUNTS = {"themes": 13, "criteria": 106, "tests": 258}
SOURCE_COMMIT = "ca4019f95073b6cbd2482a16e9f12b52d8de678d"
DATA_ROOT = Path(__file__).with_name("data") / RGAA_VERSION
EXPECTED_SOURCE_HASHES = {
    "criteres.json": "25f71c18150d15514253badd883d0c62e329bc3928814779dc4b41497002a13a",
    "methodologies.json": "199dcd99b3c9783465c74936e721f2d905bfbd286ff6a3197358912e2ba1b84d",
    "glossaire.json": "6855035e03fd9c47aee743976897f9958539e5b2be88996a41a1b273e29e01a4",
}
EXPECTED_CATALOG_HASH = "a9ed7e0ac58d4b3f66f6cd33bb7263da709d483c6619d7866ba68c1fb543dac3"
EXPECTED_MATRIX_HASH = "b301426f03c1c30a98b2c8bdb8021440507579fceb55be55354e94cd753276a7"


class CatalogError(ValueError):
    """The vendored normative catalog is incomplete or has drifted."""


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise CatalogError(f"RGAA catalog file unavailable: {path.name}") from error


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(f"RGAA catalog file invalid: {path.name}") from error


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    for name, expected in EXPECTED_SOURCE_HASHES.items():
        observed = _sha256(DATA_ROOT / name)
        if observed != expected:
            raise CatalogError(f"RGAA source integrity mismatch: {name}")
    catalog_path = DATA_ROOT / "catalog.json"
    if _sha256(catalog_path) != EXPECTED_CATALOG_HASH:
        raise CatalogError("RGAA generated catalog integrity mismatch")
    matrix_path = DATA_ROOT / "automation-matrix.json"
    if _sha256(matrix_path) != EXPECTED_MATRIX_HASH:
        raise CatalogError("RGAA automation matrix integrity mismatch")
    manifest = _read(DATA_ROOT / "source-manifest.json")
    if manifest.get("source_commit") != SOURCE_COMMIT:
        raise CatalogError("RGAA source commit mismatch")
    catalog = _read(catalog_path)
    matrix = _read(matrix_path)
    if catalog.get("schema") != "cdpx.rgaa.catalog/v1":
        raise CatalogError("unsupported RGAA catalog schema")
    if catalog.get("rgaa_version") != RGAA_VERSION:
        raise CatalogError("unsupported RGAA version")
    if catalog.get("counts") != EXPECTED_COUNTS:
        raise CatalogError("RGAA catalog cardinality drift")
    tests = catalog.get("tests")
    if not isinstance(tests, list) or len(tests) != EXPECTED_COUNTS["tests"]:
        raise CatalogError("RGAA catalog test inventory drift")
    ids = [test.get("id") for test in tests if isinstance(test, dict)]
    matrix_ids = [entry.get("official_test_id") for entry in matrix if isinstance(entry, dict)]
    if len(ids) != len(set(ids)) or set(ids) != set(matrix_ids):
        raise CatalogError("RGAA catalog/matrix join drift")
    return catalog


def catalog_sha256() -> str:
    load_catalog()
    return EXPECTED_CATALOG_HASH


def test_index() -> dict[str, dict[str, Any]]:
    return {test["id"]: test for test in load_catalog()["tests"]}


def parse_test_selection(raw: str | None) -> tuple[str, ...] | None:
    if raw is None or not raw.strip():
        return None
    selected = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    known = test_index()
    unknown = [test_id for test_id in selected if test_id not in known]
    if unknown:
        raise CatalogError(f"unknown RGAA test id(s): {', '.join(unknown)}")
    return selected


def describe_catalog(selected: tuple[str, ...] | None = None) -> dict[str, Any]:
    catalog = load_catalog()
    wanted = set(selected) if selected is not None else None
    tests = [
        {
            "id": test["id"],
            "theme_id": test["theme_id"],
            "theme_title": test["theme_title"],
            "criterion_id": test["criterion_id"],
            "criterion_title": test["criterion_title"],
            "statement": test["statement"],
            "automation": test["automation"],
        }
        for test in catalog["tests"]
        if wanted is None or test["id"] in wanted
    ]
    return {
        "schema": "cdpx.rgaa.catalog-summary/v1",
        "catalog_id": CATALOG_ID,
        "rgaa_version": RGAA_VERSION,
        "source_commit": SOURCE_COMMIT,
        "catalog_sha256": catalog_sha256(),
        "counts": catalog["counts"],
        "selected": len(tests),
        "runtime_fetch": False,
        "tests": tests,
    }
