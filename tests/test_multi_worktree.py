from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from cdpx import proof
from cdpx.artifacts import ArtifactError
from cdpx.proofing.suites import compose_project_name

FAKE_DOCKER = """#!/bin/sh
set -eu
printf '%s|%s|%s\n' "$PWD" "${CDPX_SITE_SYMFONY_BASE:-}" "$*" >> "$CDPX_TEST_DOCKER_LOG"
case "${1:-}" in
    info) exit 0 ;;
    context)
        printf 'unix://%s\n' "$CDPX_TEST_DOCKER_SOCKET"
        ;;
    compose)
        case " $* " in
            *" port symfony 8000 "*) printf '127.0.0.1:%s\n' "$CDPX_TEST_SITE_PORT" ;;
        esac
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
    site_port: int = 18000,
) -> None:
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "CDPX_TEST_DOCKER_LOG": str(log),
        "CDPX_TEST_DOCKER_SOCKET": str(docker_socket),
        "CDPX_TEST_SITE_PORT": str(site_port),
    }
    subprocess.run([str(root / "dev"), *arguments], cwd=root, env=env, check=True)


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.isolate-parallel-worktrees",
    proves=["Distinct worktrees emit disjoint Docker and artifact namespaces."],
)
def test_two_worktrees_emit_disjoint_docker_and_artifact_namespaces(tmp_path):
    """The host portal scopes images, Compose resources, mounts and caches
    by canonical worktree, while site recording accepts a dynamic host port."""

    roots = [tmp_path / "alpha" / "cdpx", tmp_path / "beta" / "cdpx"]
    for root in roots:
        root.mkdir(parents=True)
        shutil.copy2("dev", root / "dev")

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
        for root, log in zip(roots, logs, strict=True):
            _run_dev(root, fake_bin, docker_socket, log, "setup")
            _run_dev(root, fake_bin, docker_socket, log, "check-local")
        for index, (root, log) in enumerate(zip(roots, logs, strict=True), start=1):
            _run_dev(
                root,
                fake_bin,
                docker_socket,
                log,
                "site-record",
                "record",
                site_port=18000 + index,
            )
    finally:
        listener.close()

    identities = [_identity(root) for root in roots]
    assert identities[0] != identities[1]
    assert compose_project_name(roots[0]) != compose_project_name(roots[1])

    transcripts = [log.read_text(encoding="utf-8") for log in logs]
    for index, (root, identity, transcript) in enumerate(
        zip(roots, identities, transcripts, strict=True), start=1
    ):
        dev_image = f"cdpx-dev:wt-{identity}"
        runtime_image = f"cdpx-runtime:wt-{identity}"
        assert f"--set dev.tags={dev_image}" in transcript
        assert f"--set runtime.tags={runtime_image}" in transcript
        assert dev_image in transcript
        assert f"source={root.resolve()},target={root.resolve()}" in transcript
        assert f"HOME={root.resolve()}/.cache/home" in transcript
        assert f"-p cdpx-site-casts-{identity}" in transcript
        assert f"http://host.docker.internal:{18000 + index}" in transcript

    for identity in identities:
        assert f"cdpx-dev:wt-{identity}" not in transcripts[1 - identities.index(identity)]
        assert f"cdpx-runtime:wt-{identity}" not in transcripts[1 - identities.index(identity)]
        assert f"cdpx-site-casts-{identity}" not in transcripts[1 - identities.index(identity)]
    assert str(roots[0] / ".cache") not in transcripts[1]
    assert str(roots[1] / ".cache") not in transcripts[0]


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
