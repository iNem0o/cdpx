"""CLI du générateur de casts de la homepage.

Usage (depuis la racine du dépôt):

    python3 scripts/site_casts/generate.py list
    python3 scripts/site_casts/generate.py record [--only id,id] [--port 8899]
    python3 scripts/site_casts/generate.py check

`record` exécute chaque scénario contre un Chrome réel (session supervisée
jetable) et le site témoin du dépôt, puis écrit `site/assets/casts/<id>.cast`.
Un scénario dont une attente échoue ne produit aucun cast et sort en erreur.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.site_casts.runtime import StepFailure, check_casts, record_scenario  # noqa: E402
from scripts.site_casts.scenarios import ALL_SCENARIOS  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "site" / "assets" / "casts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="lister les scénarios")
    rec = sub.add_parser("record", help="enregistrer les casts")
    rec.add_argument("--only", help="ids de scénarios, séparés par des virgules")
    rec.add_argument("--port", type=int, default=8899, help="port du site témoin")
    rec.add_argument("--out", type=Path, default=DEFAULT_OUT)
    rec.add_argument("--keep-workdir", action="store_true")
    rec.add_argument(
        "--symfony-base",
        default=os.environ.get("CDPX_SITE_SYMFONY_BASE"),
        help="URL de l'app Symfony témoin (active le scénario profiler)",
    )
    chk = sub.add_parser("check", help="valider les casts présents")
    chk.add_argument("--dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if args.command == "list":
        for scenario in ALL_SCENARIOS:
            marker = f"  (requires: {scenario.requires})" if scenario.requires else ""
            print(f"{scenario.id:12s} {scenario.title}{marker}")
        return 0

    if args.command == "check":
        report = check_casts(args.dir, ALL_SCENARIOS)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    wanted = set(args.only.split(",")) if args.only else None
    selected = [s for s in ALL_SCENARIOS if wanted is None or s.id in wanted]
    if wanted:
        unknown = wanted - {s.id for s in selected}
        if unknown:
            parser.error(f"scénarios inconnus: {', '.join(sorted(unknown))}")
    failures = 0
    for scenario in selected:
        try:
            result = record_scenario(
                scenario,
                port=args.port,
                out_dir=args.out,
                keep_workdir=args.keep_workdir,
                symfony_base=args.symfony_base,
            )
        except StepFailure as error:
            failures += 1
            result = {"id": scenario.id, "status": "failed", "error": str(error)}
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
