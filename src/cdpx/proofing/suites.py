"""Collecte d'évidence des suites: commandes de preuve et portail Symfony.

Toutes les dépendances que la façade `cdpx.proof` laisse monkeypatcher
(``_run_text``, ``_stream_to_private_file`` via ``_stream_and_collect``,
``shutil.which``, ``SYMFONY_LOG``, ``EVIDENCE_DIR``, ``PROOF_DIR``) sont
reçues ici en keyword-only: la façade les résout depuis ses globals au moment
de l'appel. Aucun symbole de ce module ne lit `cdpx.proof` à l'exécution.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from cdpx.proofing.evidence_policy import (
    SCENARIOS_SCHEMA,
    redaction_context_from_environment,
)
from cdpx.proofing.execution import (
    CommandEvidence,
    _repo_env,
    _rewrite_text_paths,
    _sanitize_argv,
)
from cdpx.proofing.execution import (
    _stream_and_collect as _default_stream_and_collect,
)
from cdpx.proofing.private_io import _now, _secure_dir, _write_private_text
from cdpx.security.redaction import RedactionContext, redact_text, redact_tree

SYMFONY_NODEID = "tests/e2e/test_e2e_symfony.py::test_profiler_reads_real_symfony_web_profiler"

StreamAndCollect = Callable[..., tuple[int, bool, str]]
RunText = Callable[..., tuple[int, str]]


def run_evidence(
    id: str,
    label: str,
    argv: list[str],
    log_path: Path,
    *,
    env: dict[str, str],
    timeout: float | None = None,
    redaction_context: RedactionContext | None = None,
    path_rewrites: Sequence[tuple[str, str]] = (),
    stream_and_collect: StreamAndCollect | None = None,
) -> CommandEvidence:
    context = redaction_context or redaction_context_from_environment()
    collect = _default_stream_and_collect if stream_and_collect is None else stream_and_collect
    started = _now()
    start = time.monotonic()
    _secure_dir(log_path.parent)
    safe_argv = _sanitize_argv(
        [_rewrite_text_paths(value, path_rewrites) for value in argv], context
    )
    header = [
        f"$ {' '.join(safe_argv)}",
        f"started_at: {started}",
        "",
        "--- output ---",
    ]
    # Flux brut streamé dans un fichier privé *.partial (progression
    # observable, mémoire bornée pendant l'exécution), puis redaction du texte
    # complet relu et écriture atomique du log final: un secret à cheval sur
    # deux chunks ne peut pas y échapper.
    exit_code, _timed_out, raw = collect(
        argv, log_path, env=env, timeout=timeout, timeout_label="commande interrompue"
    )
    output = redact_text(
        _rewrite_text_paths(raw, path_rewrites), context=context, path=f"$.commands.{id}.stdout"
    )
    duration = time.monotonic() - start
    footer = ["", "--- result ---", f"exit_code: {exit_code}", f"duration_s: {duration:.3f}", ""]
    _write_private_text(log_path, "\n".join(header) + "\n" + output + "\n".join(footer))
    return CommandEvidence(
        id=id,
        label=label,
        argv=argv,
        log=str(log_path),
        exit_code=exit_code,
        duration_s=round(duration, 3),
        status="ok" if exit_code == 0 else "failed",
    )


def _write_command_log(
    log_path: Path,
    argv: list[str],
    started: str,
    body: str,
    result: str,
    *,
    redaction_context: RedactionContext | None = None,
) -> None:
    context = redaction_context or redaction_context_from_environment()
    _secure_dir(log_path.parent)
    _write_private_text(
        log_path,
        "\n".join(
            [
                f"$ {' '.join(_sanitize_argv(argv, context))}",
                f"started_at: {started}",
                "",
                "--- output ---",
                redact_text(body, context=context, path="$.command.body").rstrip(),
                "",
                "--- result ---",
                redact_text(result, context=context, path="$.command.result").rstrip(),
                "",
            ]
        ),
    )


def write_symfony_unavailable_evidence(
    reason: str,
    *,
    redaction_context: RedactionContext | None = None,
    proof_dir: Path | None = None,
    symfony_log: Path,
    evidence_dir: Path,
) -> None:
    context = redaction_context or redaction_context_from_environment()
    # Même règle de dérivation que run_symfony_evidence: sans proof_dir, les
    # chemins résolus par la façade (monkeypatchables) font foi; le pipeline
    # passe le staging.
    log_path = symfony_log if proof_dir is None else proof_dir / symfony_log.name
    evidence_root = evidence_dir if proof_dir is None else proof_dir / evidence_dir.name
    _secure_dir(evidence_root)
    safe_reason = redact_text(reason, context=context, path="$.symfony.reason")
    payload = {
        "schema": SCENARIOS_SCHEMA,
        "suite": "symfony",
        "generated_at": _now(),
        "count": 1,
        "scenarios": [
            {
                "nodeid": SYMFONY_NODEID,
                "suite": "symfony",
                "title": "Symfony Docker portal unavailable",
                "area": "developer diagnostics",
                "feature": "dev-profiler-diff",
                "journey": "read-profiler",
                "scenario_id": "dev-profiler-diff.read-symfony-profiler",
                "proves": [
                    "Symfony Docker e2e was requested by proof generation.",
                    "Docker was unavailable, so the real Symfony scenario did not run.",
                ],
                "intent": "",
                "intent_line": 0,
                "assertions": [],
                "failed_line": 0,
                "started_at": _now(),
                "duration_s": 0.0,
                "status": "unavailable",
                "phase": "setup",
                "message": safe_reason,
                "stdout": "",
                "stderr": "",
                "artifacts": [
                    {
                        "type": "logs",
                        "label": "Symfony e2e availability log",
                        "path": str(log_path),
                        "bytes": log_path.stat().st_size if log_path.exists() else 0,
                        "mime": "text/plain",
                        "created_at": _now(),
                    }
                ],
            }
        ],
    }
    _write_private_text(
        evidence_root / "symfony-scenarios.json",
        json.dumps(redact_tree(payload, context=context), ensure_ascii=False, indent=2) + "\n",
    )


def run_symfony_evidence(
    *,
    redaction_context: RedactionContext | None = None,
    proof_dir: Path | None = None,
    timeout: float | None = None,
    path_rewrites: Sequence[tuple[str, str]] = (),
    run_text: RunText,
    stream_and_collect: StreamAndCollect,
    which: Callable[[str], str | None],
    symfony_log: Path,
    evidence_dir: Path,
    default_proof_dir: Path,
) -> CommandEvidence:
    context = redaction_context or redaction_context_from_environment()
    # Résolution à l'appel (côté façade): sans proof_dir explicite, les
    # chemins patchés par les tests font foi; le pipeline passe le staging.
    log_path = symfony_log if proof_dir is None else proof_dir / symfony_log.name
    evidence_root = evidence_dir if proof_dir is None else proof_dir / evidence_dir.name
    argv = [
        "docker",
        "compose",
        "-f",
        "docker-compose.symfony-e2e.yml",
        "up",
        "--build",
        "--abort-on-container-exit",
        "--exit-code-from",
        "cdpx",
    ]
    started = _now()
    start = time.monotonic()
    _secure_dir(evidence_root)
    compose_env = _repo_env()
    compose_env["CDPX_E2E_UID"] = str(os.getuid())
    compose_env["CDPX_E2E_GID"] = str(os.getgid())
    # Le volume `.proof` du compose est paramétré: le conteneur monte l'arbre
    # cible (staging pendant `make proof`, `./.proof` par défaut via Makefile).
    compose_env["CDPX_PROOF_DIR"] = str(
        (default_proof_dir if proof_dir is None else proof_dir).resolve()
    )

    checks: list[str] = []
    if which("docker") is None:
        reason = "Docker CLI not found; Symfony e2e is required for release proof."
        _write_command_log(
            log_path,
            argv,
            started,
            reason,
            "status: unavailable\nexit_code: 1",
            redaction_context=context,
        )
        write_symfony_unavailable_evidence(
            reason,
            redaction_context=context,
            proof_dir=proof_dir,
            symfony_log=symfony_log,
            evidence_dir=evidence_dir,
        )
        return CommandEvidence(
            id="symfony-e2e",
            label="Symfony E2E Docker",
            argv=argv,
            log=str(log_path),
            exit_code=1,
            duration_s=round(time.monotonic() - start, 3),
            status="unavailable",
        )

    for check_argv in (["docker", "compose", "version"], ["docker", "info"]):
        code, output = run_text(check_argv, timeout=15, env=compose_env)
        checks.append(f"$ {' '.join(check_argv)}\n{output.rstrip()}\nexit_code: {code}")
        if code != 0:
            reason = (
                "Docker is installed but unavailable; Symfony e2e is required for release proof."
            )
            body = "\n\n".join(checks + [reason])
            _write_command_log(
                log_path,
                argv,
                started,
                body,
                "status: unavailable\nexit_code: 1",
                redaction_context=context,
            )
            write_symfony_unavailable_evidence(
                reason,
                redaction_context=context,
                proof_dir=proof_dir,
                symfony_log=symfony_log,
                evidence_dir=evidence_dir,
            )
            return CommandEvidence(
                id="symfony-e2e",
                label="Symfony E2E Docker",
                argv=argv,
                log=str(log_path),
                exit_code=1,
                duration_s=round(time.monotonic() - start, 3),
                status="unavailable",
            )

    down_argv = [
        "docker",
        "compose",
        "-f",
        "docker-compose.symfony-e2e.yml",
        "down",
        "--remove-orphans",
        # --volumes: aucun volume déclaré aujourd'hui, mais si une image de
        # base ajoute un VOLUME, ses volumes anonymes ne s'accumulent pas.
        "--volumes",
    ]
    pre_code, pre_output = run_text(down_argv, timeout=60, env=compose_env)
    try:
        # Le `up` est streamé dans un fichier privé (progression observable)
        # et borné par deadline: kill du groupe sur dépassement, exit 124.
        up_code, _up_timed_out, up_output = stream_and_collect(
            argv,
            log_path,
            env=compose_env,
            timeout=timeout,
            timeout_label="docker compose up interrompu",
        )
    finally:
        # Même une interruption/exception/deadline pendant `up` doit rendre la
        # main avec les conteneurs et réseaux Compose supprimés.
        post_code, post_output = run_text(down_argv, timeout=60, env=compose_env)
    duration = time.monotonic() - start
    body = "\n\n".join(
        checks
        + [
            f"$ {' '.join(down_argv)}\n{pre_output.rstrip()}\nexit_code: {pre_code}",
            f"$ {' '.join(argv)}\n{up_output.rstrip()}\nexit_code: {up_code}",
            f"$ {' '.join(down_argv)}\n{post_output.rstrip()}\nexit_code: {post_code}",
        ]
    )
    result_code = up_code if up_code != 0 else post_code
    _write_command_log(
        log_path,
        argv,
        started,
        _rewrite_text_paths(body, path_rewrites),
        f"exit_code: {result_code}\nduration_s: {duration:.3f}",
        redaction_context=context,
    )
    return CommandEvidence(
        id="symfony-e2e",
        label="Symfony E2E Docker",
        argv=argv,
        log=str(log_path),
        exit_code=result_code,
        duration_s=round(duration, 3),
        status="ok" if result_code == 0 else "failed",
    )
