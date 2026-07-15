"""Producteur asciinema opt-in — dégradation propre, jamais bloquant."""

import subprocess

import pytest

from cdpx.proofing import cast
from cdpx.security.redaction import RedactionContext


def test_cast_enabled_requires_env_flag_and_binary(monkeypatch):
    monkeypatch.setattr(cast.shutil, "which", lambda name: "/usr/bin/asciinema")
    #: le flag seul ne suffit pas, le binaire seul non plus
    assert cast.cast_enabled({}) is False
    assert cast.cast_enabled({"CDPX_PROOF_CAST": "1"}) is True

    monkeypatch.setattr(cast.shutil, "which", lambda name: None)
    assert cast.cast_enabled({"CDPX_PROOF_CAST": "1"}) is False


def test_record_cast_returns_none_when_asciinema_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(cast.shutil, "which", lambda name: None)

    #: binaire absent => None, l'entrée "optional" du catalogue couvre le cas
    assert cast.record_cast("demo", ["true"], tmp_path / "demo.cast", env={}) is None


def test_record_cast_degrades_on_failure_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr(cast.shutil, "which", lambda name: "/usr/bin/asciinema")
    cast_path = tmp_path / "demo.cast"

    def failing_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="boom")

    monkeypatch.setattr(cast.subprocess, "run", failing_run)
    #: échec d'enregistrement => statut dégradé, aucun fichier laissé derrière
    assert cast.record_cast("demo", ["true"], cast_path, env={})["status"] == "unavailable"
    assert not cast_path.exists()

    def oversized_run(*args, **kwargs):
        cast_path.write_text('{"version": 2}\n' + "x" * 64, encoding="utf-8")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

    monkeypatch.setattr(cast.subprocess, "run", oversized_run)
    monkeypatch.setattr(cast, "MAX_CAST_BYTES", 10)
    #: un cast trop gros est supprimé plutôt que d'alourdir la preuve
    assert cast.record_cast("demo", ["true"], cast_path, env={})["status"] == "too-large"
    assert not cast_path.exists()


def test_record_cast_redacts_and_secures_the_recording(tmp_path, monkeypatch):
    monkeypatch.setattr(cast.shutil, "which", lambda name: "/usr/bin/asciinema")
    cast_path = tmp_path / "demo.cast"

    def fake_run(*args, **kwargs):
        cast_path.write_text(
            '{"version": 2, "width": 80}\n[0.1, "o", "token proof-canary-42"]\n',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

    monkeypatch.setattr(cast.subprocess, "run", fake_run)
    context = RedactionContext.from_secrets(["proof-canary-42"])

    entry = cast.record_cast("demo", ["true"], cast_path, env={}, redaction_context=context)

    assert entry["status"] == "generated"
    content = cast_path.read_text(encoding="utf-8")
    #: le .cast écrit sur disque est redacté avant toute lecture par le cockpit
    assert "proof-canary-42" not in content
    assert "***" in content
    assert oct(cast_path.stat().st_mode & 0o777) == "0o600"


def test_export_gif_requires_agg_and_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr(cast.shutil, "which", lambda name: None)
    assert cast.export_gif(tmp_path / "a.cast", tmp_path / "a.gif", env={}) is None

    monkeypatch.setattr(cast.shutil, "which", lambda name: "/usr/bin/agg")

    def failing_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=2, stdout="no ttf")

    monkeypatch.setattr(cast.subprocess, "run", failing_run)
    assert cast.export_gif(tmp_path / "a.cast", tmp_path / "a.gif", env={})["status"] == (
        "unavailable"
    )


def test_collect_cast_evidence_is_strictly_opt_in(tmp_path, monkeypatch):
    monkeypatch.setattr(cast.shutil, "which", lambda name: "/usr/bin/asciinema")

    def forbidden_run(*args, **kwargs):  # pragma: no cover - garde-fou
        raise AssertionError("aucun enregistrement ne doit partir sans opt-in")

    monkeypatch.setattr(cast.subprocess, "run", forbidden_run)
    #: sans CDPX_PROOF_CAST=1, aucune commande n'est exécutée
    assert cast.collect_cast_evidence(tmp_path, env={}, environ={}) == []


def test_collect_cast_evidence_records_each_demo_command(tmp_path, monkeypatch):
    monkeypatch.setattr(cast.shutil, "which", lambda name: "/usr/bin/asciinema")
    recorded = []

    def fake_run(argv, **kwargs):
        if argv[0] == "asciinema":
            target = argv[-1]
            with open(target, "w", encoding="utf-8") as stream:
                stream.write('{"version": 2}\n[0.1, "o", "ok"]\n')
            recorded.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="")

    monkeypatch.setattr(cast.subprocess, "run", fake_run)

    entries = cast.collect_cast_evidence(tmp_path, env={}, environ={"CDPX_PROOF_CAST": "1"})

    assert [entry["status"] for entry in entries] == ["generated"] * len(cast.CAST_COMMANDS)
    assert len(recorded) == len(cast.CAST_COMMANDS)
    #: agg répond aussi à which dans ce test => un GIF est tenté et rattaché
    assert all("gif" in entry for entry in entries)


def test_evidence_catalog_lists_generated_gifs(tmp_path, monkeypatch):
    from cdpx import proof

    monkeypatch.setattr(proof, "PROOF_DIR", tmp_path)
    (tmp_path / "cli-help.gif").write_bytes(b"GIF89a\x00")
    (tmp_path / "cli-help.cast").write_text('{"version": 2}\n', encoding="utf-8")

    catalog = proof.build_evidence_catalog({"commands": []}, {}, {}, {})

    types = {item["type"] for item in catalog}
    assert "gif" in types and "asciinema" in types
    #: dès qu'un vrai cast existe, l'entrée placeholder "optional" disparaît
    assert not any(item["type"] == "asciinema" and item["status"] == "optional" for item in catalog)


@pytest.mark.parametrize("value", ["", "0", "yes"])
def test_cast_env_only_accepts_the_documented_value(value, monkeypatch):
    monkeypatch.setattr(cast.shutil, "which", lambda name: "/usr/bin/asciinema")
    assert cast.cast_enabled({"CDPX_PROOF_CAST": value}) is False
