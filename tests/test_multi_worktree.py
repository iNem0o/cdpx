from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cdpx import proof
from cdpx.artifacts import ArtifactError
from cdpx.proofing.suites import compose_project_name

FAKE_DOCKER = r"""#!/bin/sh
set -eu
printf '%s|%s|%s\n' "$PWD" "${CDPX_SITE_SYMFONY_BASE:-}" "$*" >> "$CDPX_TEST_DOCKER_LOG"
case "${1:-}" in
    info) exit 0 ;;
    context)
        printf 'unix://%s\n' "$CDPX_TEST_DOCKER_SOCKET"
        ;;
esac
"""


def _identity(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]


def _run_dev(
    root: Path,
    fake_bin: Path,
    docker_socket: Path,
    log: Path,
    *arguments: str,
) -> None:
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "CDPX_TEST_DOCKER_LOG": str(log),
        "CDPX_TEST_DOCKER_SOCKET": str(docker_socket),
        #: internal recorder variable: a poisoned ambient value must never
        #: attach any command to a foreign network
        "DEV_NETWORK": "wt-poison",
    }
    # The assertions target the worktree-scoped defaults, not a CI alias.
    env.pop("CDPX_DEV_IMAGE", None)
    env.pop("CDPX_RUNTIME_IMAGE", None)
    # A lock-handling regression must fail the suite, never hang it.
    subprocess.run(
        [str(root / "dev"), *arguments],
        cwd=root,
        env=env,
        check=True,
        timeout=60,
    )


def _make_worktree_id(root: Path) -> str:
    completed = subprocess.run(
        ["make", "-s", "worktree-id"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.isolate-parallel-worktrees",
    proves=["Distinct worktrees emit disjoint Docker and artifact namespaces."],
)
def test_two_worktrees_emit_disjoint_docker_and_artifact_namespaces(tmp_path):
    """The host portal scopes images, Compose resources, mounts and caches
    by canonical worktree, and the site recorder joins the stack's network
    instead of publishing a host port."""

    roots = [tmp_path / "alpha" / "cdpx", tmp_path / "beta" / "cdpx"]
    for root in roots:
        root.mkdir(parents=True)
        shutil.copy2("dev", root / "dev")
        shutil.copy2("Makefile", root / "Makefile")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(FAKE_DOCKER, encoding="utf-8")
    fake_docker.chmod(0o755)
    docker_socket = tmp_path / "docker.sock"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(docker_socket))
    try:
        logs = [tmp_path / "alpha.log", tmp_path / "beta.log"]

        def exercise(root: Path, log: Path) -> None:
            _run_dev(root, fake_bin, docker_socket, log, "setup")
            _run_dev(root, fake_bin, docker_socket, log, "check-local")
            _run_dev(root, fake_bin, docker_socket, log, "site-record", "record")

        #: the sequences run concurrently: a regression that serializes
        #: distinct worktrees behind a shared lock trips the _run_dev timeout
        with ThreadPoolExecutor(max_workers=len(roots)) as pool:
            futures = [
                pool.submit(exercise, root, log) for root, log in zip(roots, logs, strict=True)
            ]
            for future in futures:
                future.result()
    finally:
        listener.close()

    identities = [_identity(root) for root in roots]
    assert identities[0] != identities[1]

    #: one identity oracle: the Makefile facade, the ./dev portal (asserted on
    #: the transcripts below) and the Python proof suites must all agree.
    for root, identity in zip(roots, identities, strict=True):
        assert _make_worktree_id(root) == identity
        assert compose_project_name(root) == f"cdpx-gate-{identity}"

    transcripts = [log.read_text(encoding="utf-8") for log in logs]
    for root, identity, transcript in zip(roots, identities, transcripts, strict=True):
        assert f"--set dev.tags=cdpx-dev:wt-{identity}" in transcript
        assert f"--set runtime.tags=cdpx-runtime:wt-{identity}" in transcript
        assert f"source={root.resolve()},target={root.resolve()}" in transcript
        assert f"HOME={root.resolve()}/.cache/home" in transcript
        assert f"-p cdpx-site-casts-{identity}" in transcript
        #: the recorder reaches Symfony over the stack network, never a
        #: published host port
        assert f"--network cdpx-site-casts-{identity}_default" in transcript
        assert "|http://symfony:8000|" in transcript
        assert "wt-poison" not in transcript

    for mine, other in ((0, 1), (1, 0)):
        identity = identities[mine]
        assert f"cdpx-dev:wt-{identity}" not in transcripts[other]
        assert f"cdpx-runtime:wt-{identity}" not in transcripts[other]
        assert f"cdpx-site-casts-{identity}" not in transcripts[other]
        assert str(roots[mine] / ".cache") not in transcripts[other]


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.isolate-parallel-worktrees",
    proves=["Distinct worktrees emit disjoint Docker and artifact namespaces."],
)
def test_proof_lock_refuses_two_writers_in_one_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(proof, "PROOF_DIR", tmp_path / ".proof")

    with proof._exclusive_proof_lock():
        with pytest.raises(ArtifactError, match="proof already running"):
            with proof._exclusive_proof_lock():
                pass


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.isolate-parallel-worktrees",
    proves=["Distinct worktrees emit disjoint Docker and artifact namespaces."],
)
def test_proof_locks_of_distinct_worktrees_coexist(tmp_path, monkeypatch):
    """The proof lock is scoped by PROOF_DIR: two worktrees hold theirs
    simultaneously while a second writer in either one is still refused."""

    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()

    monkeypatch.setattr(proof, "PROOF_DIR", alpha / ".proof")
    with proof._exclusive_proof_lock():
        monkeypatch.setattr(proof, "PROOF_DIR", beta / ".proof")
        with proof._exclusive_proof_lock():
            with pytest.raises(ArtifactError, match="proof already running"):
                with proof._exclusive_proof_lock():
                    pass


def test_proof_lock_rejects_symlinked_lock_path(tmp_path, monkeypatch):
    monkeypatch.setattr(proof, "PROOF_DIR", tmp_path / ".proof")

    victim = tmp_path / "victim"
    victim.write_text("precious\n", encoding="utf-8")
    (tmp_path / ".proof.lock").symlink_to(victim)

    with pytest.raises(ArtifactError, match="not openable|regular proof lock"):
        with proof._exclusive_proof_lock():
            pass
    assert victim.read_text(encoding="utf-8") == "precious\n"
