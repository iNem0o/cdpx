"""Producteur cast natif (pty → asciicast v2) — portail bloquant, jamais d'exception."""

import json
import sys

from cdpx.proofing import cast
from cdpx.security.redaction import RedactionContext


def _events(cast_path):
    lines = cast_path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    return header, [json.loads(line) for line in lines[1:]]


def test_record_cast_produces_valid_asciicast_v2(tmp_path, evidence_case):
    """L'enregistreur natif produit un .cast v2 lisible par le player, sans
    binaire externe: header conforme, évènements 'o' horodatés croissants."""
    cast_path = tmp_path / "demo.cast"

    entry = cast.record_cast(
        "demo",
        [sys.executable, "-c", "print('ligne un'); print('ligne deux')"],
        cast_path,
        env={"PATH": "/usr/bin"},
    )

    #: l'enregistrement aboutit et pointe vers le fichier écrit
    assert entry["status"] == "generated"
    assert entry["path"] == str(cast_path)
    header, events = _events(cast_path)
    #: le header respecte le contrat asciicast v2 attendu par le player xterm
    assert header["version"] == 2
    assert header["width"] == cast.CAST_WIDTH and header["height"] == cast.CAST_HEIGHT
    #: chaque évènement est une sortie 'o' et le temps ne recule jamais
    assert events and all(event[1] == "o" for event in events)
    times = [event[0] for event in events]
    assert times == sorted(times)
    assert "ligne un" in "".join(event[2] for event in events)

    if evidence_case is not None:
        evidence_case.attach_cast(cast_path, "Enregistrement asciicast v2 (demo)")


def test_record_cast_runs_on_a_real_pty(tmp_path):
    """La commande enregistrée voit un vrai TTY: c'est ce qui rend le cast
    fidèle (couleurs, largeur) contrairement à une capture de pipe."""
    cast_path = tmp_path / "tty.cast"

    entry = cast.record_cast(
        "tty",
        [sys.executable, "-c", "import sys; print(sys.stdout.isatty())"],
        cast_path,
        env={"PATH": "/usr/bin"},
    )

    _header, events = _events(cast_path)
    #: le sous-processus s'est bien exécuté attaché à un pseudo-terminal
    assert entry["status"] == "generated"
    assert "True" in "".join(event[2] for event in events)


def test_record_cast_degrades_on_failure_size_and_timeout(tmp_path, monkeypatch):
    """Tout échec retourne un statut dégradé sans lever ni laisser de fichier:
    le portail juge les statuts, l'enregistreur reste inoffensif."""
    cast_path = tmp_path / "demo.cast"

    #: une commande qui sort en erreur ne produit pas de preuve
    entry = cast.record_cast(
        "demo", [sys.executable, "-c", "raise SystemExit(3)"], cast_path, env={}
    )
    assert entry["status"] == "unavailable"
    assert not cast_path.exists()

    monkeypatch.setattr(cast, "MAX_CAST_BYTES", 10)
    #: un cast trop gros est supprimé plutôt que d'alourdir la preuve
    entry = cast.record_cast("demo", [sys.executable, "-c", "print('x' * 64)"], cast_path, env={})
    assert entry["status"] == "too-large"
    assert not cast_path.exists()
    monkeypatch.setattr(cast, "MAX_CAST_BYTES", 2 * 1024 * 1024)

    #: le timeout tue l'enregistrement et le marque indisponible
    entry = cast.record_cast(
        "demo",
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cast_path,
        env={},
        timeout=0.5,
    )
    assert entry["status"] == "unavailable"
    assert not cast_path.exists()


def test_record_cast_degrades_when_command_cannot_start(tmp_path):
    """Un binaire introuvable est un statut dégradé, pas une exception."""
    entry = cast.record_cast("demo", ["/nonexistent/binary"], tmp_path / "demo.cast", env={})

    #: OSError au démarrage => unavailable, aucun fichier laissé derrière
    assert entry["status"] == "unavailable"
    assert not (tmp_path / "demo.cast").exists()


def test_record_cast_redacts_and_secures_the_recording(tmp_path):
    """Le .cast écrit sur disque est redacté avant toute lecture par le
    cockpit et reste privé (0600)."""
    cast_path = tmp_path / "demo.cast"
    context = RedactionContext.from_secrets(["proof-canary-42"])

    entry = cast.record_cast(
        "demo",
        [sys.executable, "-c", "print('token proof-canary-42')"],
        cast_path,
        env={},
        redaction_context=context,
    )

    assert entry["status"] == "generated"
    content = cast_path.read_text(encoding="utf-8")
    #: le canari n'atteint jamais le disque, le marqueur de redaction si
    assert "proof-canary-42" not in content
    assert "***" in content
    assert oct(cast_path.stat().st_mode & 0o777) == "0o600"


def test_collect_cast_evidence_records_every_demo_command(tmp_path, monkeypatch):
    """La collecte n'est plus opt-in: chaque commande de CAST_COMMANDS produit
    une entrée jugée par le portail, sans variable d'environnement."""
    monkeypatch.setattr(
        cast,
        "CAST_COMMANDS",
        (
            ("un", [sys.executable, "-c", "print('un')"]),
            ("deux", [sys.executable, "-c", "print('deux')"]),
        ),
    )

    entries = cast.collect_cast_evidence(tmp_path, env={})

    #: une entrée par commande de démonstration, toutes générées
    assert [entry["id"] for entry in entries] == ["un", "deux"]
    assert [entry["status"] for entry in entries] == ["generated", "generated"]
    assert (tmp_path / "un.cast").is_file() and (tmp_path / "deux.cast").is_file()


def test_cast_commands_include_cli_help_and_mock_demo():
    """Les démos embarquées restent bon marché et sans navigateur: l'aide CLI
    et la session mock supervisée."""
    ids = [cast_id for cast_id, _argv in cast.CAST_COMMANDS]

    #: le contrat des démonstrations enregistrées est explicite
    assert ids == ["cli-help", "mock-session-demo"]
    for _cast_id, argv in cast.CAST_COMMANDS:
        assert argv[0] == sys.executable
