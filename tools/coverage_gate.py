"""Enforce line and branch coverage independently."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def percentages(report: dict) -> tuple[float, float]:
    totals = report["totals"]
    statements = int(totals["num_statements"])
    branches = int(totals["num_branches"])
    line = 100.0 if statements == 0 else 100 * int(totals["covered_lines"]) / statements
    branch = 100.0 if branches == 0 else 100 * int(totals["covered_branches"]) / branches
    return line, branch


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        print("usage: python -m tools.coverage_gate REPORT LINE_MIN BRANCH_MIN", file=sys.stderr)
        return 2
    report = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    line, branch = percentages(report)
    line_min, branch_min = float(args[1]), float(args[2])
    print(
        json.dumps(
            {
                "line": round(line, 2),
                "line_min": line_min,
                "branch": round(branch, 2),
                "branch_min": branch_min,
            },
            separators=(",", ":"),
        )
    )
    return 0 if line >= line_min and branch >= branch_min else 1


if __name__ == "__main__":
    raise SystemExit(main())
