"""Single command registry for local development, CI and release gates."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tools.bump import bump


@dataclass(frozen=True)
class Gate:
    name: str
    command: tuple[str, ...]


LOCAL_GATES = (
    Gate("ruff-check", ("ruff", "check", "src", "tests", "tools")),
    Gate("ruff-format", ("ruff", "format", "--check", "src", "tests", "tools")),
    Gate("mypy", ("mypy", "src/cdpx", "tools")),
    Gate(
        "unit",
        (
            "pytest",
            "tests",
            "--ignore=tests/e2e",
            "--cov=cdpx",
            "--cov-branch",
            "--cov-report=term",
            "--cov-report=json:.coverage.json",
            "--cov-fail-under=0",
        ),
    ),
    Gate(
        "coverage-thresholds",
        (sys.executable, "-m", "tools.coverage_gate", ".coverage.json", "85", "75"),
    ),
)


def run(command: tuple[str, ...] | list[str]) -> None:
    rendered = " ".join(command)
    print(f"==> {rendered}", file=sys.stderr, flush=True)
    subprocess.run(command, check=True)


def check_local() -> None:
    for gate in LOCAL_GATES:
        run(gate.command)


def check() -> None:
    # The proof pipeline is the full gate and already collects Ruff, mypy,
    # unit, real Chrome and Symfony evidence exactly once.
    run((sys.executable, "-m", "cdpx.proof"))


def format_sources() -> None:
    run(("ruff", "format", "src", "tests", "tools"))
    run(("ruff", "check", "src", "tests", "tools", "--fix"))


def clean() -> None:
    for path in (
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".coverage",
        ".proof",
        ".proof.new",
        ".proof.old",
        "dist",
        "build",
    ):
        candidate = Path(path)
        if candidate.is_dir():
            shutil.rmtree(candidate)
        else:
            candidate.unlink(missing_ok=True)
    for cache in Path(".").glob("**/__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache)
    print(json.dumps({"cleaned": True}, separators=(",", ":")))


def build_internal() -> None:
    Path("dist").mkdir(exist_ok=True)
    run(("uv", "build", "--wheel", "--out-dir", "dist"))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m tools.harness")
    root.add_argument(
        "command",
        choices=("check-local", "check", "proof", "release", "fmt", "clean", "test-e2e", "bump"),
    )
    root.add_argument("version", nargs="?", help="target X.Y.Z, required by bump")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.version is not None and args.command != "bump":
        parser().error(f"{args.command} takes no version argument")
    try:
        if args.command == "check-local":
            check_local()
        elif args.command in {"check", "proof"}:
            check()
        elif args.command == "release":
            check()
            build_internal()
        elif args.command == "fmt":
            format_sources()
        elif args.command == "clean":
            clean()
        elif args.command == "bump":
            if args.version is None:
                parser().error("bump requires a target version: bump X.Y.Z")
            bump(args.version)
        else:
            run(("pytest", "tests/e2e", "-v"))
    except subprocess.CalledProcessError as error:
        return error.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
