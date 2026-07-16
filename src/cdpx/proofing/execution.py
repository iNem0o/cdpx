"""Exécution bornée des commandes de preuve et utilitaires de réécriture.

Les fonctions dont la façade `cdpx.proof` permet le monkeypatch dans les tests
(`_stream_to_private_file` notamment) sont reçues ici en paramètre keyword-only
par leurs consommateurs: aucun symbole de ce module ne lit `cdpx.proof` à
l'exécution.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdpx.artifacts import ArtifactError
from cdpx.proofing.private_io import _secure_dir
from cdpx.security.redaction import RedactionContext, redact_text
from cdpx.testing.evidence import PROOF_RETENTION_ENV

# `CDPX_PROOF_TIMEOUT_SCALE` (flottant strictement positif, ex. "2" sur machine
# lente) multiplie uniformément tous les budgets de deadline de la preuve.
PROOF_TIMEOUT_SCALE_ENV = "CDPX_PROOF_TIMEOUT_SCALE"

_ALLOWED_ENV_NAMES = {
    "CI",
    "COLORTERM",
    "HOME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LOGNAME",
    "NO_COLOR",
    "PATH",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    PROOF_RETENTION_ENV,
    "SHELL",
    "TERM",
    "TMPDIR",
    "TZ",
    "USER",
    "VIRTUAL_ENV",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_RUNTIME_DIR",
}

StreamToPrivateFile = Callable[..., tuple[int, bool]]


@dataclass
class CommandEvidence:
    id: str
    label: str
    argv: list[str]
    log: str
    exit_code: int
    duration_s: float
    status: str


def proof_timeout_scale(environ: dict[str, str] | None = None) -> float:
    """Facteur d'échelle des deadlines, validé fail-closed comme la rétention."""

    values = os.environ if environ is None else environ
    raw = values.get(PROOF_TIMEOUT_SCALE_ENV)
    if raw is None:
        return 1.0
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)?", raw) or float(raw) <= 0:
        raise ValueError(f"{PROOF_TIMEOUT_SCALE_ENV} doit être un flottant strictement positif")
    return float(raw)


def _sanitize_argv(argv: list[str], context: RedactionContext) -> list[str]:
    return [
        redact_text(value, context=context, path=f"$.argv[{index}]")
        for index, value in enumerate(argv)
    ]


def _repo_env() -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if name in _ALLOWED_ENV_NAMES}
    src = str(Path("src").resolve())
    env["PYTHONPATH"] = src
    return env


def _rewrite_text_paths(value: str, rewrites: Sequence[tuple[str, str]]) -> str:
    """Réécrit les chemins d'une racine physique vers sa racine logique.

    La réécriture est ancrée: seuls les préfixes de chemin `racine/…` et la
    valeur exactement égale à la racine sont réécrits. Un littéral nu (ex.
    `.proof.new` cité dans un extrait de code capturé par l'évidence) est
    préservé tel quel — un remplacement naïf corromprait ces extraits.
    """

    for physical, logical in rewrites:
        if value == physical:
            value = logical
            continue
        value = value.replace(f"{physical}/", f"{logical}/")
    return value


def _read_json_or_fail(path: Path, label: str) -> Any:
    """Lit un JSON en échouant fermé avec une erreur localisée.

    Le fichier fautif et la cause sont nommés dans l'ArtifactError, plutôt
    qu'une OSError/JSONDecodeError anonyme au milieu du pipeline de preuve.
    """

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"{label}: {path}: {exc}") from exc


def _rewrite_tree_paths(value: Any, rewrites: Sequence[tuple[str, str]]) -> Any:
    """Applique les réécritures de chemins à toutes les chaînes d'un arbre JSON."""

    if isinstance(value, str):
        return _rewrite_text_paths(value, rewrites)
    if isinstance(value, list):
        return [_rewrite_tree_paths(item, rewrites) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_tree_paths(item, rewrites) for key, item in value.items()}
    return value


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Tue le groupe de processus entier d'une commande de preuve.

    ``proc.kill()`` seul ne toucherait que l'enfant direct: un Chrome ou un
    serveur de fixtures lancé par pytest survivrait à la deadline, garderait
    ses ports et pourrait écrire dans l'évidence après la purge.
    """

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Groupe déjà disparu ou inaccessible: repli sur l'enfant direct.
        proc.kill()
    proc.wait()


def _stream_to_private_file(
    argv: list[str],
    sink: Path,
    *,
    env: dict[str, str],
    timeout: float | None,
) -> tuple[int, bool]:
    """Exécute ``argv`` en streamant stdout+stderr bruts dans ``sink`` (0600).

    La sortie n'est jamais bufferisée en mémoire: le fichier grossit au fil de
    l'exécution (observable via tail -f) et la deadline est monotone. Retourne
    (exit_code, timed_out): 127 si le binaire est introuvable, 124 après kill
    sur dépassement de deadline.
    """

    _secure_dir(sink.parent)
    if sink.is_symlink():
        raise ArtifactError(f"lien symbolique interdit: {sink}")
    sink.unlink(missing_ok=True)
    fd = os.open(sink, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        try:
            # start_new_session isole la commande dans son propre groupe de
            # processus: le kill de deadline atteint aussi ses descendants.
            proc = subprocess.Popen(
                argv,
                cwd=Path.cwd(),
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            stream.write((str(exc) + "\n").encode("utf-8"))
            return 127, False
        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Deadline dépassée: kill du groupe puis drain du statut. Ce qui a
            # déjà été streamé dans le fichier reste disponible pour le log.
            _kill_process_group(proc)
            return 124, True
        except BaseException:
            # Exception inattendue (KeyboardInterrupt, MemoryError…): ne
            # jamais rendre la main en laissant le groupe tourner.
            _kill_process_group(proc)
            raise
    return exit_code, False


def _stream_and_collect(
    argv: list[str],
    log_path: Path,
    *,
    env: dict[str, str],
    timeout: float | None,
    timeout_label: str,
    stream: StreamToPrivateFile | None = None,
) -> tuple[int, bool, str]:
    """Streame ``argv`` dans un flux privé ``*.partial`` puis relit sa sortie.

    Le flux brut (NON redacté) est supprimé en toutes circonstances — même si
    la lecture, la redaction ou l'écriture finale échoue: un staging partiel
    conservé pour diagnostic ne doit jamais contenir de sortie brute. La
    mémoire n'est bornée que PENDANT l'exécution (streaming disque): la
    relecture finale recharge tout le texte pour la redaction — choix assumé,
    la deadline borne la durée du run, pas le volume de sa sortie.

    ``stream`` reçoit l'implémentation de streaming (contrat de façade: la
    façade `cdpx.proof` la résout au moment de l'appel pour rester
    monkeypatchable dans les tests).
    """

    stream_impl = _stream_to_private_file if stream is None else stream
    partial = log_path.with_name(f"{log_path.name}.partial")
    try:
        exit_code, timed_out = stream_impl(argv, partial, env=env, timeout=timeout)
        raw = partial.read_text(encoding="utf-8", errors="replace")
    finally:
        partial.unlink(missing_ok=True)
    if timed_out:
        raw += f"\ntimeout: {timeout_label} après {timeout}s (exit 124)\n"
    return exit_code, timed_out, raw


def _run_text(
    argv: list[str],
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=Path.cwd(),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return 124, output + f"\ntimeout after {timeout}s\n"
    except FileNotFoundError as exc:
        return 127, f"{exc}\n"
    return proc.returncode, proc.stdout
