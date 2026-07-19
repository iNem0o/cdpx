"""Git context of the proof run and derived review packs.

``collect_git_context`` receives as keyword-only what the `cdpx.proof`
facade lets tests monkeypatch (``run_text``) or derives from its patchable
constants (paths, excludes): no symbol in this module reads `cdpx.proof` at
runtime.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from cdpx.proofing.evidence_policy import redaction_context_from_environment
from cdpx.proofing.private_io import _write_private_text
from cdpx.security.redaction import (
    RedactionContext,
    redact_text,
)

GENERATED_PREFIXES = (".proof/", ".idea/")
PRIVATE_WORKTREE_PREFIXES = ("AGENTS.md", "article/", "presentation/")

RunText = Callable[..., tuple[int, str]]


def collect_git_context(
    *,
    redaction_context: RedactionContext | None = None,
    status_path: Path,
    diff_stat_path: Path,
    run_text: RunText,
    timeout: float,
    diff_excludes: Sequence[str],
) -> dict:
    context = redaction_context or redaction_context_from_environment()
    branch_code, branch = run_text(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout)
    sha_code, sha = run_text(["git", "rev-parse", "--short", "HEAD"], timeout)
    status_code, status = run_text(["git", "status", "--short"], timeout)
    stat_code, stat = run_text(
        ["git", "diff", "--stat", "--", ".", *diff_excludes],
        timeout,
    )

    # A failed git output (timeout 124, broken repository) is not
    # porcelain: partial output and a timeout annotation would produce
    # corrupted entries. We neither parse nor publish anything — the
    # status_code and diff_stat_code already exposed in the summary are
    # enough for diagnostics.
    if status_code != 0:
        status = ""
    if stat_code != 0:
        stat = ""
    safe_status_lines = []
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if path == "AGENTS.md" or path.startswith(PRIVATE_WORKTREE_PREFIXES[1:]):
            continue
        safe_status_lines.append(line)
    status = redact_text("\n".join(safe_status_lines), context=context, path="$.git.status")
    if status:
        status += "\n"
    stat = redact_text(stat, context=context, path="$.git.diff_stat")
    _write_private_text(status_path, status)
    _write_private_text(diff_stat_path, stat)

    changed_files = []
    generated_files = []
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        item = {"status": line[:2].strip() or "?", "path": path}
        if path.startswith(GENERATED_PREFIXES):
            generated_files.append(item)
        else:
            changed_files.append(item)

    return {
        "branch": redact_text(branch.strip(), context=context, path="$.git.branch")
        if branch_code == 0
        else "unknown",
        "sha": sha.strip() if sha_code == 0 else "unknown",
        "status_code": status_code,
        "diff_stat_code": stat_code,
        "changed_files": changed_files,
        "generated_files": generated_files,
        "changed_count": len(changed_files),
        "generated_count": len(generated_files),
        "status_path": str(status_path),
        "diff_stat_path": str(diff_stat_path),
    }


def classify_change(path: str) -> str:
    if path.startswith("src/"):
        return "Product code"
    if path.startswith("tests/"):
        return "Tests"
    if path.startswith("docs/") or path in {
        "README.md",
        "HARNESS.md",
        "CLAUDE.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "SUPPORT.md",
    }:
        return "Documentation"
    if path in {"Makefile", "pyproject.toml", "Dockerfile"} or path.startswith(".github/"):
        return "Harness / CI"
    return "Other"


def build_impact_map(git_context: dict, help_commands: list[dict[str, str]]) -> dict:
    changed_files = git_context["changed_files"]
    categories: dict[str, list[str]] = {}
    for item in changed_files:
        categories.setdefault(classify_change(item["path"]), []).append(item["path"])

    paths = {item["path"] for item in changed_files}
    entrypoints = []
    if "Makefile" in paths:
        entrypoints.append(
            {
                "name": "make proof",
                "type": "Make target",
                "evidence": "Makefile",
                "review_focus": "Public command that generates the report.",
            }
        )
    if "src/cdpx/proof.py" in paths:
        entrypoints.append(
            {
                "name": "python -m cdpx.proof",
                "type": "Python module",
                "evidence": "src/cdpx/proof.py",
                "review_focus": "Collection, classification and HTML rendering of proofs.",
            }
        )
    if "tests/test_proof.py" in paths:
        entrypoints.append(
            {
                "name": "tests/test_proof.py",
                "type": "Unit tests",
                "evidence": "tests/test_proof.py",
                "review_focus": "JUnit parsing, CLI help and published summary fields.",
            }
        )

    change_types = []
    if any(path.startswith("src/") for path in paths):
        change_types.append("code")
    if any(path.startswith("tests/") for path in paths):
        change_types.append("tests")
    if "Makefile" in paths or any(path.startswith(".github/") for path in paths):
        change_types.append("harness")
    if any(path.startswith("docs/") or path in {"README.md", "HARNESS.md"} for path in paths):
        change_types.append("docs")
    if help_commands:
        change_types.append("verified-cli-surface")

    return {
        "change_types": change_types or ["unknown"],
        "categories": categories,
        "entrypoints": entrypoints,
    }


def build_review_guide(impact: dict) -> dict:
    order = []
    categories = impact["categories"]
    if "Harness / CI" in categories:
        order.append("Start with the Makefile: check the user contract of `make proof`.")
    if "Product code" in categories:
        order.append("Read `src/cdpx/proof.py`: collection, verdict, JSON summary, HTML rendering.")
    if "Tests" in categories:
        order.append("Read `tests/test_proof.py`: parsing and published summary fields.")
    if "Documentation" in categories:
        order.append("Finish with README/HARNESS/VALIDATION: alignment of the public contract.")
    if not order:
        order.append("Read the files listed in the impact map, from entrypoint to proofs.")

    watch_outs = [
        "The verdict must be derived from commands and JUnit, not a static status.",
        "Heavy artifacts must remain collapsible and traceable to avoid PR noise.",
        "Proof paths must remain relative and openable from the repository.",
        "Missing optional proofs must be declared as unknowns, not simulated.",
    ]
    return {"order": order, "watch_outs": watch_outs}


def build_risks_and_unknowns(git_context: dict) -> dict:
    risks = [
        {
            "risk": "`make proof` becomes stricter.",
            "mitigation": (
                "Python tools go through `python -m ...`; the report is written even on failure."
            ),
            "rollback": "Revert to the previous Makefile target if needed.",
        },
        {
            "risk": "Report too verbose for a PR.",
            "mitigation": "Short summary; logs and secondary details in collapsible sections.",
            "rollback": "Reduce the sections in `render_html` without touching the collection.",
        },
    ]
    unknowns = [
        {
            "item": "Exact GitHub rendering of the HTML",
            "why": "The report is an HTML artifact, not a page rendered in the GitHub PR.",
            "how_to_verify": (
                "Download the `proof` artifact then open `.proof/proof-report.html`."
            ),
        },
        {
            "item": "Demonstration casts",
            "why": (
                "The native recorder (pty) is part of the gate: a missing "
                "or degraded cast fails `make proof`."
            ),
            "how_to_verify": "Open the report and play the casts from the proof catalog.",
        },
        {
            "item": "Product screenshot",
            "why": "Harness/report change, not a product UI delta.",
            "how_to_verify": "For a UI PR, add a capture in `.proof/`.",
        },
    ]
    if git_context["generated_count"]:
        unknowns.append(
            {
                "item": "Versioned generated artifacts",
                "why": "The repository already tracks some `.proof` files.",
                "how_to_verify": (
                    "Check `git status --short`; `.proof/` must remain a gitignored CI artifact."
                ),
            }
        )
    return {"risks": risks, "unknowns": unknowns}


def build_project_risks_and_unknowns() -> dict:
    risks = [
        {
            "risk": "Chrome/Chromium prerequisite mandatory.",
            "mitigation": (
                "Chrome/Chromium is mandatory: `make proof` fails if the binary is missing."
            ),
            "rollback": "Install Chrome/Chromium then re-run `make test-e2e` or `make proof`.",
        },
        {
            "risk": "Docker/Compose is a prerequisite of the full quality gate.",
            "mitigation": (
                "`make check`, `make proof` and `make release` fail if Docker or the Symfony "
                "proof is unavailable; `make check-local` remains a partial diagnostic."
            ),
            "rollback": "Install Docker then re-run `make proof` or `make docker-symfony-e2e`.",
        },
    ]
    unknowns = [
        {
            "item": "External network dependencies",
            "why": "`make proof` targets local fixtures and local Chrome.",
            "how_to_verify": "Check the network logs and fixtures under `tests/fixtures/`.",
        },
        {
            "item": "Scope of visual captures",
            "why": (
                "E2E captures are kept in the private `.proof/evidence/` tree "
                "and excluded from shareable staging; they do not constitute an "
                "exhaustive visual diff."
            ),
            "how_to_verify": (
                "Inspect the private catalog and add a dedicated assertion or baseline "
                "for any visual regression to be contracted."
            ),
        },
        {
            "item": "Full run cast",
            "why": (
                "The gate natively records the demonstration commands; "
                "the entire `make proof` run is not auto-recorded (duration and weight)."
            ),
            "how_to_verify": (
                "Demonstration casts are generated and judged at every `make proof`; "
                "to record the full run, launch `make proof` inside an "
                "external terminal recorder."
            ),
        },
    ]
    return {"risks": risks, "unknowns": unknowns}
