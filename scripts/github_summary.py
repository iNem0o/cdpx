#!/usr/bin/env python3
"""Render the compact GitHub Actions summary from release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


def _load_summary(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, f"{path} is absent (the release gate stopped before proof generation)"
    except (json.JSONDecodeError, OSError) as exc:
        return {}, f"{path} is unreadable: {exc}"
    if not isinstance(payload, dict):
        return {}, f"{path} does not contain a JSON object"
    return payload, None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _commit() -> str:
    if value := os.environ.get("GITHUB_SHA"):
        return value
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return "unknown"
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def _command_status(summary: dict[str, Any], command_id: str) -> str:
    for command in summary.get("commands", []):
        if command.get("id") == command_id:
            return str(command.get("status", "unknown"))
    return "unavailable"


def build_report(
    summary: dict[str, Any],
    *,
    summary_error: str | None,
    dist_dir: Path,
    artifact_name: str,
    release_outcome: str,
) -> tuple[str, dict[str, Any]]:
    archives = []
    if dist_dir.is_dir():
        for path in sorted(item for item in dist_dir.iterdir() if item.is_file()):
            archives.append(
                {
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                    "kind": "wheel" if path.suffix == ".whl" else "sdist",
                }
            )
    has_wheel = any(item["kind"] == "wheel" for item in archives)
    has_sdist = any(item["name"].endswith(".tar.gz") for item in archives)
    proof_ok = summary.get("ok") is True
    packaging_ok = release_outcome == "success" and has_wheel and has_sdist
    overall_ok = proof_ok and packaging_ok and summary_error is None
    state = "PASS" if overall_ok else "FAIL"
    icon = "✅" if overall_ok else "❌"
    project = summary.get("project", {})
    totals = summary.get("totals", {})
    features = summary.get("feature_inventory", {})
    feature_totals = features.get("totals", {}) if isinstance(features, dict) else {}
    junit = summary.get("junit", {})
    chrome = junit.get("e2e", {})
    symfony = junit.get("symfony", {})
    version = project.get("version", "unavailable")
    cli_count = summary.get("cli_command_count", project.get("cli_command_count", 0))
    violations = feature_totals.get("violations", len(features.get("violations", [])))
    warnings = feature_totals.get("warnings", len(features.get("warnings", [])))
    packaging = {
        "ok": packaging_ok,
        "release_outcome": release_outcome,
        "twine_strict": "passed" if packaging_ok else "failed-or-unavailable",
        "isolated_wheel_install": "passed" if packaging_ok else "failed-or-unavailable",
        "cli_contract": "passed" if packaging_ok and cli_count == 30 else "failed-or-unavailable",
        "archive_contents": "passed" if packaging_ok else "failed-or-unavailable",
        "archives": archives,
    }
    lines = [
        f"## {icon} cdpx PR proof: {state}",
        "",
        "| Signal | Result |",
        "| --- | --- |",
        f"| Commit | `{_commit()}` |",
        f"| Version | `{version}` |",
        (
            "| Tests | "
            f"{totals.get('passed', 0)} passed · {totals.get('failed', 0)} failed · "
            f"{totals.get('skipped', 0)} skipped · {totals.get('unavailable', 0)} unavailable |"
        ),
        (
            f"| Chrome | {_command_status(summary, 'e2e')} · "
            f"{chrome.get('tests', 0)} tests · {chrome.get('skipped', 0)} skipped |"
        ),
        (
            f"| Symfony | {_command_status(summary, 'symfony-e2e')} · "
            f"{symfony.get('tests', 0)} tests · {symfony.get('skipped', 0)} skipped |"
        ),
        f"| CLI contract | {cli_count} commands |",
        f"| Feature catalogue | {violations} violations · {warnings} warnings |",
        (
            f"| Packaging | wheel={'yes' if has_wheel else 'no'} · "
            f"sdist={'yes' if has_sdist else 'no'} · release gate={release_outcome} |"
        ),
        f"| Cockpit artifact | `{artifact_name}` (30 days) |",
    ]
    if summary_error:
        lines.extend(["", f"> {summary_error}"])
    failures = summary.get("proof_failures", [])
    if failures:
        lines.extend(["", "### Proof failures", ""])
        lines.extend(f"- {failure}" for failure in failures[:20])
    lines.extend(
        [
            "",
            "The artifact contains every available cockpit file, JUnit report, log, screenshot, "
            "packaging diagnostic, wheel, and sdist. Checkboxes in the PR description do not "
            "replace `PR Gate / Required`.",
        ]
    )
    return "\n".join(lines) + "\n", packaging


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, default=Path(".proof/validation-summary.json"))
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--output-dir", type=Path, default=Path(".ci-artifacts"))
    parser.add_argument("--artifact-name", required=True)
    parser.add_argument("--release-outcome", required=True)
    args = parser.parse_args()

    summary, error = _load_summary(args.summary)
    markdown, packaging = build_report(
        summary,
        summary_error=error,
        dist_dir=args.dist_dir,
        artifact_name=args.artifact_name,
        release_outcome=args.release_outcome,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "github-summary.md").write_text(markdown, encoding="utf-8")
    (args.output_dir / "packaging-summary.json").write_text(
        json.dumps(packaging, indent=2) + "\n", encoding="utf-8"
    )
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
