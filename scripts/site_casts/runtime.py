"""Runtime d'enregistrement des casts tutoriels de la homepage.

Doctrine identique aux casts historiques (site/assets/casts/README.md):
chaque sortie JSON et chaque durée proviennent de commandes réellement
exécutées contre un Chrome réel et le site témoin du dépôt; seule la frappe
clavier est synthétisée (cadence déterministe) pour la lisibilité. Le cast
n'est écrit que si toutes les attentes (`expect`, code de sortie) sont
vérifiées: un scénario rouge ne produit aucun artefact.
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

# Palette alignée sur les casts historiques et le thème de la page.
ANSI_PROMPT = "\x1b[38;5;242m$\x1b[0m \x1b[1m"
ANSI_RESET = "\x1b[0m"
ANSI_OUT = "\x1b[38;5;80m"
ANSI_ERR = "\x1b[38;5;167m"
ANSI_COMMENT = "\x1b[3;38;5;246m"

# Cadence de frappe déterministe (secondes/caractère), même esprit que les
# casts historiques: jitter cyclique autour de ~30 ms.
_TYPE_CYCLE = (0.022, 0.03, 0.038, 0.026, 0.034, 0.03)
_COMMENT_CYCLE = (0.010, 0.014, 0.012)


@dataclasses.dataclass(frozen=True)
class Comment:
    """Ligne `# ...` pédagogique, affichée en italique dim."""

    text: str


@dataclasses.dataclass(frozen=True)
class Cmd:
    """Une commande cdpx: argv réel exécuté, affichage `cdpx ...` synthétisé."""

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
    """Ligne shell complète (pipes jq, diff...) exécutée via bash -c."""

    command: str
    display: str | None = None
    expect: tuple[str, ...] = ()
    expect_exit: int = 0
    env: dict[str, str] = dataclasses.field(default_factory=dict)
    timeout: float = 90.0


Step = Comment | Cmd | Shell


@dataclasses.dataclass(frozen=True)
class Scenario:
    """Un cast tutoriel: titre, étapes, prérequis d'environnement."""

    id: str
    title: str
    steps: tuple[Step, ...]
    height: int = CAST_HEIGHT
    manage_session: bool = True
    authority: str = "privileged"
    env: dict[str, str] = dataclasses.field(default_factory=dict)
    # Fichiers copiés dans le cwd d'exécution: (source relative au dépôt,
    # nom destination, substitutions {placeholder: valeur, "{base}" résolu}).
    copies: tuple[tuple[str, str, dict[str, str]], ...] = ()
    # Sous-chaînes interdites dans le cast final (fuite de secret).
    forbidden: tuple[str, ...] = ()
    requires: str | None = None  # ex: "symfony" => sauté par défaut


class StepFailure(RuntimeError):
    """Une attente d'étape n'est pas vérifiée; le cast n'est pas écrit."""


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
    """Accumule les évènements asciicast v2 avec une horloge monotone."""

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
    """Extrait les `export NAME='value'` d'une sortie --export."""

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
            f"[{label}] exit {code} (attendu {step.expect_exit})\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )
    for needle in step.expect:
        if needle not in stdout:
            raise StepFailure(
                f"[{label}] sortie sans «{needle}»\nstdout: {stdout}\nstderr: {stderr}"
            )


def _make_cdpx_shim(bin_dir: Path) -> None:
    """Un vrai `cdpx` dans PATH pour les étapes Shell (pipes jq, diff)."""

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
    """Exécute un scénario contre Chrome réel + fixtures et écrit son cast."""

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
                # Ligne vide avant un bloc de commentaires: sépare visuellement
                # le titre de l'étape suivante de la sortie JSON précédente.
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
                        raise StepFailure(f"[{display}] aucune ligne export capturée")
                    run_env.update(exports)
                    exports_seen = True
                    builder.clock += min(elapsed, 8.0)  # eval "$(...)" n'affiche rien
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
            raise StepFailure(f"[{scenario.id}] valeur interdite présente dans le cast: {needle}")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_CAST_BYTES:
        raise StepFailure(f"[{scenario.id}] cast trop volumineux: {len(encoded)} octets")
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
    """Valide les casts présents: format v2, horloge monotone, interdits."""

    reports: list[dict[str, Any]] = []
    ok = True
    for scenario in scenarios:
        path = out_dir / f"{scenario.id}.cast"
        report: dict[str, Any] = {"id": scenario.id, "path": str(path)}
        if not path.is_file():
            # Un scénario à prérequis absent n'est pas une erreur; les autres si.
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
            assert header["width"] == CAST_WIDTH, "largeur inattendue"
            previous = 0.0
            for raw in lines[1:]:
                clock, kind, text = json.loads(raw)
                assert kind == "o", "évènement non-output"
                assert clock >= previous, "horloge non monotone"
                assert isinstance(text, str), "payload non textuel"
                previous = clock
            body = "\n".join(lines)
            for needle in scenario.forbidden:
                assert needle not in body, f"valeur interdite: {needle}"
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
