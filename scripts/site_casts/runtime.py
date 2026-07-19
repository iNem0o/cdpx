"""Runtime for recording the homepage's tutorial casts.

The recording contract is defined in site/assets/casts/README.md: every
JSON output and every duration comes from commands actually executed against
a real Chrome and the repo's reference site; only the keystroke typing is
synthesized (deterministic cadence) for readability. The cast is written
only if every expectation (`expect`, exit code) is verified: a red scenario
produces no artifact.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SRC_ROOT))

from cdpx.session import start_session, stop_session  # noqa: E402
from cdpx.testing.fixture_server import FixtureServer  # noqa: E402

CAST_WIDTH = 100
CAST_HEIGHT = 14
MAX_CAST_BYTES = 2 * 1024 * 1024

# Palette aligned with the page theme.
ANSI_PROMPT = "\x1b[38;5;242m$\x1b[0m \x1b[1m"
ANSI_RESET = "\x1b[0m"
ANSI_OUT = "\x1b[38;5;80m"
ANSI_ERR = "\x1b[38;5;167m"
ANSI_COMMENT = "\x1b[3;38;5;246m"

# Deterministic typing cadence (seconds/char): cyclic jitter around ~30 ms.
_TYPE_CYCLE = (0.022, 0.03, 0.038, 0.026, 0.034, 0.03)
_COMMENT_CYCLE = (0.010, 0.014, 0.012)


@dataclasses.dataclass(frozen=True)
class Comment:
    """Educational `# ...` line, displayed in dim italics."""

    text: str


@dataclasses.dataclass(frozen=True)
class Cmd:
    """A cdpx command: real argv executed, synthesized `cdpx ...` display."""

    argv: tuple[str, ...]
    display: str | None = None
    expect: tuple[str, ...] = ()
    expect_exit: int = 0
    env: dict[str, str] = dataclasses.field(default_factory=dict)
    timeout: float = 90.0
    capture_exports: bool = False
    show_stdout: bool = True


@dataclasses.dataclass(frozen=True)
class Shell:
    """Full shell line (jq pipes, diff...) executed via bash -c."""

    command: str
    display: str | None = None
    expect: tuple[str, ...] = ()
    expect_exit: int = 0
    env: dict[str, str] = dataclasses.field(default_factory=dict)
    timeout: float = 90.0


Step = Comment | Cmd | Shell


@dataclasses.dataclass(frozen=True)
class Scenario:
    """A tutorial cast: title, steps, environment prerequisites."""

    id: str
    title: str
    steps: tuple[Step, ...]
    height: int = CAST_HEIGHT
    manage_session: bool = True
    authority: str = "privileged"
    env: dict[str, str] = dataclasses.field(default_factory=dict)
    # Files copied into the execution cwd: (source relative to the repo,
    # destination name, substitutions {placeholder: value, "{base}" resolved}).
    copies: tuple[tuple[str, str, dict[str, str]], ...] = ()
    # Substrings forbidden in the final cast (secret leak).
    forbidden: tuple[str, ...] = ()
    requires: str | None = None  # e.g. "symfony" => skipped by default


class StepFailure(RuntimeError):
    """A step expectation is not verified; the cast is not written."""


def _substitute(value: str, base: str, symfony: str | None = None) -> str:
    value = value.replace("{base}", base)
    if symfony:
        value = value.replace("{symfony}", symfony)
    return value


def _quote(arg: str) -> str:
    if not arg or any(c in arg for c in " \"'$&|<>*?#;()"):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


class CastBuilder:
    """Accumulates asciicast v2 events with a monotonic clock."""

    def __init__(self) -> None:
        self.events: list[tuple[float, str]] = []
        self.clock = 0.5

    def emit(self, text: str, *, dt: float = 0.0) -> None:
        self.clock += dt
        self.events.append((self.clock, text))

    def type_text(self, text: str, cycle: tuple[float, ...]) -> None:
        for index, char in enumerate(text):
            self.emit(char, dt=cycle[index % len(cycle)])

    def comment(self, text: str) -> None:
        self.emit(ANSI_COMMENT, dt=0.35)
        self.type_text(f"# {text}", _COMMENT_CYCLE)
        self.emit(ANSI_RESET + "\r\n", dt=0.15)

    def prompt_command(self, display: str) -> None:
        self.emit(ANSI_PROMPT, dt=0.45)
        self.type_text(display, _TYPE_CYCLE)
        self.emit(ANSI_RESET + "\r\n", dt=0.2)

    def output(self, text: str, *, elapsed: float, color: str = ANSI_OUT) -> None:
        if not text:
            self.clock += min(elapsed, 8.0)
            return
        body = text.rstrip("\n").replace("\n", "\r\n")
        self.emit(f"{color}{body}{ANSI_RESET}\r\n", dt=min(elapsed, 8.0))

    def render(self, title: str, height: int) -> str:
        header = {
            "version": 2,
            "width": CAST_WIDTH,
            "height": height,
            "title": title,
            "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        }
        lines = [json.dumps(header, ensure_ascii=False)]
        lines.extend(
            json.dumps([round(clock, 3), "o", text], ensure_ascii=False)
            for clock, text in self.events
        )
        return "\n".join(lines) + "\n"


def _parse_exports(stdout: str) -> dict[str, str]:
    """Extracts the `export NAME='value'` lines from a --export output."""

    exports: dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("export ") and "=" in line:
            name, _, value = line[len("export ") :].partition("=")
            exports[name.strip()] = value.strip().strip("'\"")
    return exports


def _check_expectations(step: Cmd | Shell, stdout: str, stderr: str, code: int) -> None:
    label = step.display or " ".join(getattr(step, "argv", ()) or (getattr(step, "command", ""),))
    if code != step.expect_exit:
        raise StepFailure(
            f"[{label}] exit {code} (expected {step.expect_exit})\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )
    for needle in step.expect:
        if needle not in stdout:
            raise StepFailure(
                f"[{label}] output missing «{needle}»\nstdout: {stdout}\nstderr: {stderr}"
            )


def _make_cdpx_shim(bin_dir: Path) -> None:
    """A real `cdpx` on PATH for Shell steps (jq pipes, diff)."""

    shim = bin_dir / "cdpx"
    shim.write_text(
        "#!/bin/bash\n"
        f'export PYTHONPATH="{SRC_ROOT}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
        f'exec "{sys.executable}" -m cdpx.cli "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)


def record_scenario(
    scenario: Scenario,
    *,
    port: int,
    out_dir: Path,
    keep_workdir: bool = False,
    symfony_base: str | None = None,
) -> dict[str, Any]:
    """Runs a scenario against a real Chrome + fixtures and writes its cast."""

    if scenario.requires == "symfony" and not symfony_base:
        return {"id": scenario.id, "status": "skipped", "requires": scenario.requires}

    builder = CastBuilder()
    started = time.monotonic()
    workdir = Path(tempfile.mkdtemp(prefix=f"cdpx-site-cast-{scenario.id}-"))
    bin_dir = workdir / ".bin"
    bin_dir.mkdir()
    _make_cdpx_shim(bin_dir)

    server = FixtureServer(root=FIXTURES_ROOT, port=port).start()
    base = server.base_url
    sub = symfony_base.rstrip("/") if symfony_base else None
    run_env: dict[str, str] = {
        **os.environ,
        "PYTHONPATH": f"{SRC_ROOT}{os.pathsep}{os.environ.get('PYTHONPATH', '')}".rstrip(
            os.pathsep
        ),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "TERM": "xterm-256color",
        "COLUMNS": str(CAST_WIDTH),
        **{k: _substitute(v, base, sub) for k, v in scenario.env.items()},
    }
    for src_rel, dst_name, subst in scenario.copies:
        content = (REPO_ROOT / src_rel).read_text(encoding="utf-8")
        for placeholder, value in subst.items():
            content = content.replace(placeholder, _substitute(value, base, sub))
        destination = workdir / dst_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    manifest = None
    manifest_path: Path | None = None
    exports_seen = False
    try:
        if scenario.manage_session:
            manifest, manifest_path = start_session(
                run_id=f"site-{scenario.id}",
                authority=scenario.authority,
                origins="http://127.0.0.1:*",
                ttl=900.0,
                owner_pid=os.getpid(),
                browser_kind="chrome",
            )
            run_env["CDPX_SESSION"] = str(manifest_path)
            run_env["CDPX_RUN_ID"] = manifest.run_id
            run_env["CDPX_TARGET"] = manifest.target_id

        previous_step: Step | None = None
        for step in scenario.steps:
            if isinstance(step, Comment):
                # Blank line before a comment block: visually separates the
                # next step's title from the previous JSON output.
                if previous_step is not None and not isinstance(previous_step, Comment):
                    builder.emit("\r\n", dt=0.15)
                builder.comment(_substitute(step.text, base, sub))
                previous_step = step
                continue
            previous_step = step
            step_env = {**run_env, **{k: _substitute(v, base, sub) for k, v in step.env.items()}}
            if isinstance(step, Cmd):
                argv = [_substitute(a, base, sub) for a in step.argv]
                display = _substitute(
                    step.display or "cdpx " + " ".join(_quote(a) for a in argv), base, sub
                )
                builder.prompt_command(display)
                t0 = time.monotonic()
                proc = subprocess.run(
                    [sys.executable, "-m", "cdpx.cli", *argv],
                    env=step_env,
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=step.timeout,
                )
                elapsed = time.monotonic() - t0
                _check_expectations(step, proc.stdout, proc.stderr, proc.returncode)
                if step.capture_exports:
                    exports = _parse_exports(proc.stdout)
                    if not exports:
                        raise StepFailure(f"[{display}] no export line captured")
                    run_env.update(exports)
                    exports_seen = True
                    builder.clock += min(elapsed, 8.0)  # eval "$(...)" prints nothing
                elif step.show_stdout:
                    builder.output(proc.stdout, elapsed=elapsed)
                else:
                    builder.clock += min(elapsed, 8.0)
            else:
                command = _substitute(step.command, base, sub)
                display = _substitute(step.display or command, base, sub)
                builder.prompt_command(display)
                t0 = time.monotonic()
                proc = subprocess.run(
                    ["bash", "-c", command],
                    env=step_env,
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=step.timeout,
                )
                elapsed = time.monotonic() - t0
                _check_expectations(step, proc.stdout, proc.stderr, proc.returncode)
                color = ANSI_OUT if proc.returncode == 0 else ANSI_ERR
                builder.output(proc.stdout, elapsed=elapsed, color=color)
        builder.clock += 1.2
    finally:
        if manifest is not None and manifest_path is not None:
            stop_session(manifest_path, run_id=manifest.run_id, target_id=manifest.target_id)
        elif exports_seen:
            subprocess.run(
                [sys.executable, "-m", "cdpx.cli", "session", "stop"],
                env=run_env,
                capture_output=True,
                timeout=60,
            )
        server.stop()
        if not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)

    content = builder.render(scenario.title, scenario.height)
    for needle in scenario.forbidden:
        if needle in content:
            raise StepFailure(f"[{scenario.id}] forbidden value present in the cast: {needle}")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_CAST_BYTES:
        raise StepFailure(f"[{scenario.id}] cast too large: {len(encoded)} bytes")
    out_dir.mkdir(parents=True, exist_ok=True)
    cast_path = out_dir / f"{scenario.id}.cast"
    cast_path.write_text(content, encoding="utf-8")
    return {
        "id": scenario.id,
        "status": "generated",
        "path": str(cast_path),
        "bytes": len(encoded),
        "events": len(builder.events),
        "duration_s": round(builder.clock, 1),
        "wall_s": round(time.monotonic() - started, 1),
    }


def check_casts(out_dir: Path, scenarios: Iterable[Scenario]) -> dict[str, Any]:
    """Validates the present casts: v2 format, monotonic clock, forbidden values."""

    reports: list[dict[str, Any]] = []
    ok = True
    for scenario in scenarios:
        path = out_dir / f"{scenario.id}.cast"
        report: dict[str, Any] = {"id": scenario.id, "path": str(path)}
        if not path.is_file():
            # A scenario with a missing prerequisite isn't an error; others are.
            if scenario.requires:
                report["status"] = "skipped"
                report["requires"] = scenario.requires
            else:
                report["status"] = "missing"
                ok = False
            reports.append(report)
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            header = json.loads(lines[0])
            assert header["version"] == 2, "header version != 2"
            assert header["width"] == CAST_WIDTH, "unexpected width"
            previous = 0.0
            for raw in lines[1:]:
                clock, kind, text = json.loads(raw)
                assert kind == "o", "non-output event"
                assert clock >= previous, "non-monotonic clock"
                assert isinstance(text, str), "non-text payload"
                previous = clock
            body = "\n".join(lines)
            for needle in scenario.forbidden:
                assert needle not in body, f"forbidden value: {needle}"
            report["status"] = "ok"
            report["events"] = len(lines) - 1
            report["duration_s"] = round(previous, 1)
            report["bytes"] = path.stat().st_size
        except (AssertionError, ValueError, KeyError, IndexError) as error:
            report["status"] = "invalid"
            report["error"] = str(error)
            ok = False
        reports.append(report)
    return {"ok": ok, "casts": reports}
