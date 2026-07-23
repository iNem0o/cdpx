"""Real-Chrome E2E for reaching a local self-signed HTTPS service.

A disposable development Chrome must, by default, refuse a loopback HTTPS
server whose certificate chains to an untrusted CA — and the refusal has to
reach the CLI contract cleanly (exit 1, diagnostic on stderr). Two supported
escape hatches then let the same navigation succeed:

  * ``--ignore-tls-errors`` launches Chrome with ``--ignore-certificate-errors``;
  * ``--trust-ca-dir`` seeds a private per-session NSS trust store from a
    directory of PEM CAs (requires ``certutil`` from libnss3-tools).

The certificate material is a checked-in, long-lived (~100 year) CA + leaf
under ``tests/fixtures/tls/``: the slim development image ships neither the
``openssl`` CLI nor the ``cryptography`` package, so generating a chain at
runtime is not guaranteed, whereas a static fixture is fully deterministic.
The leaf carries ``IP:127.0.0.1`` in its SAN because navigation targets
loopback. ``certutil`` (libnss3-tools) is present in the dev/runtime images,
so scenario three exercises the real trust-store import; if it is ever
absent, that scenario skips with a reason rather than failing spuriously.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cdpx.session import (
    SessionManifest,
    find_chrome,
    load_manifest,
    stop_session,
)
from cdpx.testing.e2e import attach_cli_run

TLS_FIXTURES = Path(__file__).parents[1] / "fixtures" / "tls"
CA_CERT = TLS_FIXTURES / "ca.crt"
LEAF_CERT = TLS_FIXTURES / "leaf.crt"
LEAF_KEY = TLS_FIXTURES / "leaf.key"

_PAGE = (
    b"<!doctype html><html><head><title>cdpx tls</title></head>"
    b"<body><h1 id='tls-ok'>secure</h1></body></html>"
)


class _SilentHandler(http.server.BaseHTTPRequestHandler):
    """Serve one trivial page and stay quiet: no stderr noise per request."""

    def do_GET(self) -> None:  # noqa: N802 (http.server contract)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, *args: object) -> None:  # silence access logging
        pass


class LocalHttps:
    """Loopback HTTPS server on an ephemeral port, TLS from the leaf fixture."""

    def __init__(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(LEAF_CERT), keyfile=str(LEAF_KEY))
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SilentHandler)
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self.port = int(self._server.server_address[1])
        self.base_url = f"https://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> LocalHttps:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture()
def https_server():
    with LocalHttps() as server:
        yield server


def run_session_cli(
    *args: str,
    timeout: float = 40,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cdpx.cli", "--timeout", "30", "session", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def start_tls_session(
    *,
    run_id: str,
    origins: str,
    chrome_bin: str,
    runtime_dir: Path,
    extra_args: tuple[str, ...] = (),
) -> tuple[SessionManifest, Path]:
    proc = run_session_cli(
        "start",
        "--run-id",
        run_id,
        "--authority",
        "observation",
        "--origins",
        origins,
        "--ttl",
        "300",
        *extra_args,
        env={
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "CDPX_BUNDLED_CHROME": chrome_bin,
        },
    )
    assert proc.returncode == 0 and not proc.stderr, (
        f"session start failed: exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload["started"] is True
    path = Path(payload["manifest"])
    manifest = load_manifest(path, run_id=run_id, target_id=payload["target_id"])
    return manifest, path


def run_goto(
    manifest: SessionManifest,
    manifest_path: Path,
    url: str,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cdpx.cli",
            "--session",
            str(manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "--timeout",
            "20",
            "goto",
            url,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )


@contextlib.contextmanager
def cleanup(manifest: SessionManifest, path: Path):
    try:
        yield
    finally:
        if path.exists():
            with contextlib.suppress(Exception):
                stop_session(
                    path,
                    run_id=manifest.run_id,
                    target_id=manifest.target_id,
                    timeout=10,
                )


@pytest.mark.scenario(
    feature="state-session",
    journey="secure-local-tls",
    scenario_id="state-session.reject-untrusted-local-tls",
    proves=[
        "A default session refuses a loopback HTTPS server signed by an untrusted CA.",
        "The certificate rejection surfaces as exit 1 with a net::ERR_CERT diagnostic on stderr.",
    ],
)
def test_default_session_rejects_untrusted_local_https(https_server, tmp_path, evidence_case):
    """With neither escape hatch, navigating to the self-signed HTTPS service
    fails with a clean certificate error on the CLI contract."""
    chrome_bin = find_chrome()
    runtime_dir = tmp_path / "runtime"
    manifest, path = start_tls_session(
        run_id="e2e-tls-baseline",
        origins=https_server.base_url,
        chrome_bin=chrome_bin,
        runtime_dir=runtime_dir,
    )
    with cleanup(manifest, path):
        goto = run_goto(manifest, path, f"{https_server.base_url}/")
        attach_cli_run(evidence_case, "Baseline goto (untrusted local TLS)", goto)
        #: the certificate error is not swallowed: exit 1, empty stdout, and a
        #: diagnostic on stderr that names the Chrome network error
        assert goto.returncode == 1, (
            f"expected a certificate failure\nstdout={goto.stdout}\nstderr={goto.stderr}"
        )
        assert "navigation failed" in goto.stderr
        assert "ERR_CERT" in goto.stderr, goto.stderr


@pytest.mark.scenario(
    feature="state-session",
    journey="secure-local-tls",
    scenario_id="state-session.ignore-local-tls-errors",
    proves=[
        "--ignore-tls-errors launches Chrome with --ignore-certificate-errors.",
        "The previously rejected loopback HTTPS navigation then succeeds.",
    ],
)
def test_ignore_tls_errors_allows_untrusted_local_https(https_server, tmp_path, evidence_case):
    """``--ignore-tls-errors`` turns the certificate error into a successful
    navigation against the same self-signed HTTPS service."""
    chrome_bin = find_chrome()
    runtime_dir = tmp_path / "runtime"
    manifest, path = start_tls_session(
        run_id="e2e-tls-ignore",
        origins=https_server.base_url,
        chrome_bin=chrome_bin,
        runtime_dir=runtime_dir,
        extra_args=("--ignore-tls-errors",),
    )
    with cleanup(manifest, path):
        goto = run_goto(manifest, path, f"{https_server.base_url}/")
        attach_cli_run(evidence_case, "goto with --ignore-tls-errors", goto)
        assert goto.returncode == 0 and not goto.stderr, (
            f"navigation should succeed\nstdout={goto.stdout}\nstderr={goto.stderr}"
        )
        payload = json.loads(goto.stdout)
        #: navigation reported the loaded page over HTTPS without any bypass
        #: leaking into the JSON contract
        assert payload["ok"] is True
        assert payload["url"] == f"{https_server.base_url}/"


@pytest.mark.scenario(
    feature="state-session",
    journey="secure-local-tls",
    scenario_id="state-session.trust-local-ca-store",
    proves=[
        "--trust-ca-dir imports a PEM CA into a private per-session NSS trust store.",
        "Chrome then trusts the loopback HTTPS leaf signed by that CA and navigation succeeds.",
    ],
)
def test_trust_ca_dir_allows_local_https_signed_by_that_ca(https_server, tmp_path, evidence_case):
    """Pointing ``--trust-ca-dir`` at a directory holding the issuing CA lets
    Chrome trust the loopback leaf without any global certificate bypass."""
    certutil = os.environ.get("CDPX_CERTUTIL") or shutil.which("certutil")
    if not certutil:
        pytest.skip("certutil (libnss3-tools) unavailable; trust-store import not exercised")
    chrome_bin = find_chrome()
    runtime_dir = tmp_path / "runtime"
    #: a directory holding only the issuing CA — the leaf is served by the
    #: HTTPS fixture, never imported as a trust anchor
    ca_dir = tmp_path / "trusted-ca"
    ca_dir.mkdir()
    shutil.copyfile(CA_CERT, ca_dir / "ca.crt")
    manifest, path = start_tls_session(
        run_id="e2e-tls-trust",
        origins=https_server.base_url,
        chrome_bin=chrome_bin,
        runtime_dir=runtime_dir,
        extra_args=("--trust-ca-dir", str(ca_dir)),
    )
    with cleanup(manifest, path):
        goto = run_goto(manifest, path, f"{https_server.base_url}/")
        attach_cli_run(evidence_case, "goto with --trust-ca-dir", goto)
        assert goto.returncode == 0 and not goto.stderr, (
            f"navigation should succeed via the trusted CA\n"
            f"stdout={goto.stdout}\nstderr={goto.stderr}"
        )
        payload = json.loads(goto.stdout)
        #: the leaf validated against the imported CA, so the same navigation
        #: that failed in the baseline now loads cleanly
        assert payload["ok"] is True
        assert payload["url"] == f"{https_server.base_url}/"
