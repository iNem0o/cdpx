from __future__ import annotations

import stat
from pathlib import Path

import pytest

from cdpx.policy import PolicyError
from cdpx.sessions import supervisor as supervisor_mod
from cdpx.sessions import trust
from cdpx.sessions.trust import seed_trust_store

PEM_ONE = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBonecertoneMIIBonecertoneMIIBonecertone\n"
    "-----END CERTIFICATE-----\n"
)
PEM_TWO = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBtwocerttwoMIIBtwocerttwoMIIBtwocerttwo\n"
    "-----END CERTIFICATE-----\n"
)


def _fake_certutil(tmp_path: Path, log: Path, *, exit_code: int = 0) -> str:
    """A fake certutil that appends its arguments to ``log`` per call."""
    script = tmp_path / "fake-certutil"
    script.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexit {exit_code}\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


def _trust_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    trust_dir = tmp_path / "ca"
    trust_dir.mkdir()
    for name, content in files.items():
        (trust_dir / name).write_text(content, encoding="utf-8")
    return trust_dir


def test_seed_trust_store_creates_db_and_imports_every_pem_block(tmp_path):
    """Seeding creates the NSS database exactly once and imports every
    certificate block, splitting multi-certificate bundles so no CA past the
    first one is silently dropped."""
    log = tmp_path / "calls.log"
    certutil = _fake_certutil(tmp_path, log)
    trust_dir = _trust_dir(
        tmp_path,
        {"root.pem": PEM_ONE, "bundle.crt": PEM_ONE + "\n" + PEM_TWO},
    )
    home = tmp_path / "home"

    count = seed_trust_store(trust_dir, home, certutil=certutil)

    #: three certificates total: one single file plus a two-cert bundle
    assert count == 3
    lines = log.read_text(encoding="utf-8").splitlines()
    #: the database is initialized empty exactly once
    creations = [line for line in lines if "-N" in line and "--empty-password" in line]
    assert len(creations) == 1
    #: one trusted-CA import per PEM block, each tagged C,,
    imports = [line for line in lines if "-A" in line and "-t C,," in line]
    assert len(imports) == 3
    #: the private database lives under the session HOME at mode 0700
    nssdb = home / ".pki" / "nssdb"
    assert nssdb.is_dir()
    assert stat.S_IMODE(nssdb.stat().st_mode) == 0o700


def test_seed_trust_store_uses_sql_database_under_home(tmp_path):
    """certutil is pointed at the private sql: database nested under the
    provided HOME, never the caller's real profile."""
    log = tmp_path / "calls.log"
    certutil = _fake_certutil(tmp_path, log)
    trust_dir = _trust_dir(tmp_path, {"root.pem": PEM_ONE})
    home = tmp_path / "home"

    seed_trust_store(trust_dir, home, certutil=certutil)

    expected_db = f"sql:{home / '.pki' / 'nssdb'}"
    #: every certutil call targets the disposable per-session database
    assert all(expected_db in line for line in log.read_text(encoding="utf-8").splitlines())


def test_seed_trust_store_requires_certutil(tmp_path, monkeypatch):
    """When no certutil can be resolved, seeding fails closed with a message
    pointing at the missing tool rather than silently trusting nothing."""
    monkeypatch.delenv("CDPX_CERTUTIL", raising=False)
    monkeypatch.setattr(trust.shutil, "which", lambda _name: None)
    trust_dir = _trust_dir(tmp_path, {"root.pem": PEM_ONE})

    with pytest.raises(PolicyError, match="certutil"):
        seed_trust_store(trust_dir, tmp_path / "home")


def test_seed_trust_store_resolves_certutil_from_environment(tmp_path, monkeypatch):
    """CDPX_CERTUTIL overrides discovery so a pinned binary is honored."""
    log = tmp_path / "calls.log"
    certutil = _fake_certutil(tmp_path, log)
    monkeypatch.setenv("CDPX_CERTUTIL", certutil)
    monkeypatch.setattr(trust.shutil, "which", lambda _name: None)
    trust_dir = _trust_dir(tmp_path, {"root.pem": PEM_ONE})

    #: the environment-pinned certutil is used and the run succeeds
    assert seed_trust_store(trust_dir, tmp_path / "home") == 1


def test_seed_trust_store_fails_closed_when_certutil_errors(tmp_path):
    """A non-zero certutil exit aborts seeding: the session must not proceed
    believing a CA was trusted when it was not."""
    log = tmp_path / "calls.log"
    certutil = _fake_certutil(tmp_path, log, exit_code=1)
    trust_dir = _trust_dir(tmp_path, {"root.pem": PEM_ONE})

    with pytest.raises(PolicyError):
        seed_trust_store(trust_dir, tmp_path / "home", certutil=certutil)


def test_seed_trust_store_rejects_a_bundle_without_certificates(tmp_path):
    """A file that carries no PEM block imports nothing; seeding fails closed
    rather than returning a success that trusted zero certificates."""
    log = tmp_path / "calls.log"
    certutil = _fake_certutil(tmp_path, log)
    trust_dir = _trust_dir(tmp_path, {"junk.pem": "no certificate content here"})

    with pytest.raises(PolicyError, match="zero certificate"):
        seed_trust_store(trust_dir, tmp_path / "home", certutil=certutil)


def _bootstrap(**overrides: object) -> supervisor_mod.session.SupervisorBootstrap:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    fields = {
        "session_id": "a" * 24,
        "run_id": "run-env",
        "profile_id": "b" * 16,
        "browser_kind": "chrome",
        "authority": "observation",
        "origins": ("http://demo.test",),
        "owner_pid": None,
        "owner_start_time": None,
        "chrome_bin": "/fake/chrome",
        "startup_timeout": 60.0,
        "session_dir": "/tmp/session",
        "profile_dir": "/tmp/session/profile",
        "artifacts_dir": "/tmp/session/artifacts",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "runtime_id": "standalone",
        "ignore_tls_errors": False,
        "trust_ca_dir": None,
    }
    fields.update(overrides)
    return supervisor_mod.session.SupervisorBootstrap(**fields)


def test_chrome_environment_seeds_trust_and_points_home(tmp_path, monkeypatch):
    """For a real Chrome with a trust store, the supervisor seeds a private NSS
    database and hands Chrome a HOME under the session so it reads that db."""
    log = tmp_path / "calls.log"
    certutil = _fake_certutil(tmp_path, log)
    monkeypatch.setenv("CDPX_CERTUTIL", certutil)
    trust_dir = _trust_dir(tmp_path, {"root.pem": PEM_ONE})
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    data = _bootstrap(browser_kind="chrome", trust_ca_dir=str(trust_dir))

    env = supervisor_mod._chrome_environment(data, session_dir)

    #: HOME is redirected into the disposable session tree
    assert env is not None
    assert env["HOME"] == str(session_dir / "home")
    #: the database was actually seeded under that HOME
    assert (session_dir / "home" / ".pki" / "nssdb").is_dir()


def test_chrome_environment_is_none_without_trust_or_for_mock(tmp_path, monkeypatch):
    """No trust store, or a mock backend, means no environment override: Chrome
    inherits the ambient environment and the mock never seeds a trust store."""
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    #: a real Chrome without a trust directory keeps the inherited environment
    assert supervisor_mod._chrome_environment(_bootstrap(trust_ca_dir=None), session_dir) is None

    log = tmp_path / "calls.log"
    monkeypatch.setenv("CDPX_CERTUTIL", _fake_certutil(tmp_path, log))
    trust_dir = _trust_dir(tmp_path, {"root.pem": PEM_ONE})
    #: the mock backend ignores the trust store entirely
    mock_data = _bootstrap(browser_kind="mock", trust_ca_dir=str(trust_dir))
    assert supervisor_mod._chrome_environment(mock_data, session_dir) is None
    #: nothing was seeded for the mock
    assert not log.exists()
