from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cdpx import session as session_mod
from cdpx.policy import PolicyError
from cdpx.session import (
    SessionLease,
    SessionManifest,
    assert_session_active,
    build_chrome_command,
    find_chrome,
    load_manifest,
    remove_session_files,
    runtime_root,
    session_status,
    start_session,
    stop_session,
    write_manifest,
)

SESSION_ID = "a" * 24
PROFILE_ID = "b" * 16


def manifest_for(root: Path) -> SessionManifest:
    session_dir = root / SESSION_ID
    return SessionManifest(
        session_id=SESSION_ID,
        run_id="R1",
        profile_id=PROFILE_ID,
        browser_kind="chrome",
        authority="interaction",
        origins=("http://*.test",),
        host="127.0.0.1",
        port=9222,
        target_id="T1",
        websocket_url="ws://127.0.0.1:9222/devtools/page/T1",
        browser_pid=999_999,
        browser_start_time="linux:browser",
        supervisor_pid=999_998,
        supervisor_start_time="linux:supervisor",
        owner_pid=os.getpid(),
        owner_start_time="linux:owner",
        session_dir=str(session_dir),
        profile_dir=str(session_dir / "profile"),
        artifacts_dir=str(session_dir / "artifacts"),
        created_at="2026-07-12T00:00:00+00:00",
        expires_at="2026-07-12T01:00:00+00:00",
    )


def test_manifest_is_private_and_builds_execution_context(tmp_path):
    """L'écriture du manifest impose des permissions privées et sa relecture
    attestée restitue le même contenu, prêt à produire un contexte d'exécution
    portant l'autorité déclarée."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    #: dossier et fichier sont illisibles pour les autres utilisateurs,
    #: condition d'admission au rechargement
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = load_manifest(path, run_id="R1", target_id="T1")
    #: aucun champ ne se perd ni ne se transforme au passage sur disque
    assert loaded == manifest
    context = loaded.execution_context()
    #: le contexte hérite de l'autorité et de l'identité de session du manifest
    assert context.authority.value == "interaction"
    assert context.session_id == SESSION_ID


@pytest.mark.scenario(
    feature="state-session",
    journey="exercise-session-without-chrome",
    scenario_id="state-session.run-supervised-mock-session",
    proves=[
        "The packaged mock uses the same attested manifest and loopback endpoint contract.",
        "Stopping the mock session removes its private runtime tree.",
    ],
)
def test_mock_backend_uses_supervised_session_contract(tmp_path):
    """Le backend mock livré avec le paquet honore le même contrat de session
    supervisée que Chrome: manifest attesté, endpoint loopback cohérent, arrêt
    qui efface toute l'arborescence privée."""
    manifest, path = start_session(
        run_id="mock-contract",
        authority="privileged",
        origins="http://*.test",
        browser_kind="mock",
        owner_pid=os.getpid(),
        root=tmp_path,
        timeout=10,
    )
    session_dir = Path(manifest.session_dir)
    try:
        #: le manifest décrit le backend mock avec une URL websocket dont le
        #: port correspond à celui annoncé, comme pour un vrai Chrome
        assert manifest.browser_kind == "mock"
        assert manifest.port == int(manifest.websocket_url.split(":")[2].split("/")[0])
        #: la discovery HTTP répond sur ce port en s'identifiant comme le mock,
        #: et l'attestation d'activité passe avec le même code que pour Chrome
        assert session_mod.discovery.version(manifest.host, manifest.port)["Browser"].startswith(
            "MockChrome/"
        )
        assert_session_active(manifest)
    finally:
        stop_session(path, run_id=manifest.run_id, target_id=manifest.target_id)

    #: l'arrêt supprime le runtime privé sans laisser de trace
    assert not session_dir.exists()


def test_manifest_refuses_permissions_and_assignment_mismatch(tmp_path):
    """Le chargement du manifest échoue-fermé quand l'identité d'assignation
    (run, target) ne correspond pas, ou quand le fichier est devenu lisible
    par d'autres utilisateurs."""
    path = write_manifest(manifest_for(tmp_path))
    #: un run étranger ne peut pas s'approprier la session d'un autre
    with pytest.raises(PolicyError, match="run"):
        load_manifest(path, run_id="OTHER", target_id="T1")
    #: une cible non assignée à cette session est refusée de la même façon
    with pytest.raises(PolicyError, match="target"):
        load_manifest(path, run_id="R1", target_id="OTHER")
    path.chmod(0o644)
    #: des permissions élargies invalident le manifest, même à contenu intact
    with pytest.raises(PolicyError, match="permissions"):
        load_manifest(path, run_id="R1", target_id="T1")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("authority", "admin", "autorité"),
        ("origins", "http://demo.test", "origins"),
        ("created_at", "2026-07-12T00:00:00", "fuseau"),
        ("browser_pid", True, "browser_pid"),
        (
            "websocket_url",
            "ws://127.0.0.1:9333/devtools/page/T1",
            "port/target",
        ),
    ],
)
def test_manifest_rejects_malformed_typed_or_unbound_fields(
    tmp_path,
    field,
    value,
    message,
):
    """Chaque champ critique du manifest est validé au chargement: valeur hors
    domaine, type inattendu, datetime naïve ou URL websocket incohérente avec
    le port/target déclarés sont tous rejetés."""
    path = write_manifest(manifest_for(tmp_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    #: quelle que soit la corruption injectée, le champ fautif est nommé dans
    #: l'erreur et aucun manifest n'est jamais retourné
    with pytest.raises(PolicyError, match=message):
        load_manifest(path)


def test_manifest_rejects_tampered_session_paths(tmp_path):
    """Un manifest dont un chemin interne pointe hors du dossier de session
    est rejeté: impossible de rediriger cdpx vers un répertoire arbitraire en
    éditant le fichier."""
    path = write_manifest(manifest_for(tmp_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["profile_dir"] = "/tmp/unrelated-profile"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    #: le profil déplacé hors de l'arbre de session bloque le chargement
    with pytest.raises(PolicyError, match="hors du dossier"):
        load_manifest(path, run_id="R1", target_id="T1")


def test_session_lease_is_non_blocking_and_owned_by_run(tmp_path):
    """Le bail de session est exclusif et non bloquant: une seconde prise sur
    la même session échoue immédiatement en PolicyError au lieu d'attendre la
    libération du verrou."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    with SessionLease(path, run_id="R1", target_id="T1", require_active=False):
        #: la deuxième acquisition échoue tout de suite plutôt que de bloquer
        #: la commande concurrente
        with pytest.raises(PolicyError, match="déjà utilisée"):
            with SessionLease(path, run_id="R1", target_id="T1", require_active=False):
                pass


def test_session_lease_reattests_fresh_manifest_by_default(tmp_path, monkeypatch):
    """Par défaut, prendre le bail ré-atteste que la session est vivante et
    fournit le manifest fraîchement relu du disque."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    checked = []
    monkeypatch.setattr(session_mod, "assert_session_active", checked.append)

    with SessionLease(path, run_id="R1", target_id="T1") as leased:
        #: le bail expose le manifest relu du disque, pas une copie périmée
        assert leased == manifest

    #: l'attestation d'activité a été appelée une seule fois, sur ce manifest
    assert checked == [manifest]


def test_public_manifest_omits_capabilities_and_physical_profile(tmp_path):
    """La vue publique du manifest expose l'identité logique (run, target,
    profil éphémère) mais jamais les leviers de prise de contrôle: endpoint
    websocket, chemin physique du profil, PID du navigateur."""
    public = manifest_for(tmp_path).public_dict()
    #: l'identité logique reste consultable par l'appelant
    assert public["run_id"] == "R1" and public["target_id"] == "T1"
    assert public["profile"] == {"id": PROFILE_ID, "ephemeral": True}
    #: aucune capacité permettant d'attaquer le navigateur ou son profil ne
    #: fuit dans la sortie par défaut
    assert "websocket_url" not in public
    assert "profile_dir" not in public
    assert "browser_pid" not in public


def test_chrome_command_forces_ephemeral_loopback_profile(tmp_path):
    """La ligne de commande Chrome construite impose le confinement: debug
    joignable uniquement en loopback sur un port choisi par l'OS, et profil
    jetable dédié — jamais le Chrome personnel de l'utilisateur."""
    profile = tmp_path / "profile"
    command = build_chrome_command("/usr/bin/chromium", profile)
    assert command[0] == "/usr/bin/chromium"
    #: le debug est confiné à la loopback avec un port éphémère attribué par
    #: l'OS, donc non prévisible par un tiers
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=0" in command
    #: le profil utilisé est celui, jetable, de la session supervisée
    assert f"--user-data-dir={profile}" in command
    assert "--no-first-run" in command


def test_chrome_sandbox_is_disabled_only_for_root_or_ci(tmp_path, monkeypatch):
    """Le sandbox Chrome n'est sacrifié que là où il ne peut pas fonctionner
    (root ou CI): un utilisateur normal hors CI garde le sandbox complet."""
    monkeypatch.setattr(session_mod.os, "geteuid", lambda: 1000)
    monkeypatch.delenv("CI", raising=False)
    command = build_chrome_command("/usr/bin/chromium", tmp_path / "profile")
    #: utilisateur normal hors CI: le sandbox reste actif par défaut
    assert "--no-sandbox" not in command

    monkeypatch.setenv("CI", "true")
    command = build_chrome_command("/usr/bin/chromium", tmp_path / "profile")
    #: en CI, sandbox coupé et /dev/shm contourné (conteneurs à mémoire
    #: partagée réduite)
    assert "--no-sandbox" in command
    assert "--disable-dev-shm-usage" in command

    monkeypatch.setenv("CI", "false")
    monkeypatch.setattr(session_mod.os, "geteuid", lambda: 0)
    command = build_chrome_command("/usr/bin/chromium", tmp_path / "profile")
    #: root impose la coupure du sandbox même hors CI, mais sans
    #: l'aménagement /dev/shm propre aux conteneurs
    assert "--no-sandbox" in command
    assert "--disable-dev-shm-usage" not in command


def test_cleanup_only_removes_the_manifest_session_tree(tmp_path):
    """Le nettoyage de session est chirurgical: seul l'arbre décrit par le
    manifest disparaît, les fichiers voisins du même parent restent intacts."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    keep = tmp_path / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    remove_session_files(path)
    #: l'arbre de session est supprimé mais le fichier voisin survit intact,
    #: preuve que la suppression ne remonte pas au parent
    assert not Path(manifest.session_dir).exists()
    assert keep.read_text(encoding="utf-8") == "keep"


def test_manifest_cannot_name_an_arbitrary_parent_as_its_session(tmp_path):
    """Un manifest forgé déclarant un dossier existant quelconque comme
    session_dir est rejeté au chargement, avant qu'un nettoyage puisse viser
    ce dossier."""
    project = tmp_path / "project"
    project.mkdir(mode=0o700)
    payload = manifest_for(tmp_path)
    forged = {**payload.__dict__, "session_dir": str(project)}
    forged["profile_dir"] = str(project / "profile")
    forged["artifacts_dir"] = str(project / "artifacts")
    (project / "profile").mkdir()
    (project / "artifacts").mkdir()
    path = project / "manifest.json"
    path.write_text(json.dumps(forged), encoding="utf-8")
    path.chmod(0o600)

    #: la forge est refusée: le dossier visé n'appartient pas au runtime cdpx
    with pytest.raises(PolicyError, match="hors du dossier"):
        load_manifest(path)
    #: le répertoire ciblé par la forge n'a subi aucune destruction
    assert project.exists()


def test_stop_refuses_to_signal_a_reused_or_forged_pid(tmp_path):
    """stop_session vérifie les marqueurs d'identité du processus avant tout
    signal: un PID vivant mais qui n'est pas le Chrome de la session (PID
    recyclé ou forgé) n'est jamais tué."""
    manifest = manifest_for(tmp_path)
    process_start, _ = session_mod._process_identity(os.getpid())
    forged = replace(
        manifest,
        browser_pid=os.getpid(),
        browser_start_time=process_start,
    )
    path = write_manifest(forged)

    #: le processus courant, réel mais sans marqueur Chrome, est refusé avant
    #: l'envoi du moindre signal
    with pytest.raises(PolicyError, match="marqueur"):
        stop_session(path, run_id=forged.run_id, target_id=forged.target_id, timeout=0.001)

    #: le refus laisse le manifest en place pour diagnostic
    assert path.exists()


def test_stop_respects_the_exclusive_command_lease(tmp_path):
    """stop_session passe par le même bail exclusif que les autres commandes:
    impossible d'arrêter une session pendant qu'une commande la détient."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)

    with SessionLease(
        path,
        run_id=manifest.run_id,
        target_id=manifest.target_id,
        require_active=False,
    ):
        #: l'arrêt concurrent échoue-fermé tant que le bail est détenu par la
        #: commande en cours
        with pytest.raises(PolicyError, match="déjà utilisée"):
            stop_session(
                path,
                run_id=manifest.run_id,
                target_id=manifest.target_id,
                timeout=0.001,
            )


def test_stop_rejects_invalid_timeout_before_writing_stop_file(tmp_path):
    """Un timeout non fini est rejeté par stop_session avant tout effet de
    bord: aucun ordre d'arrêt n'est déposé dans la session."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)

    #: la validation du paramètre précède toute écriture dans la session
    with pytest.raises(PolicyError, match="fini et strictement positif"):
        stop_session(
            path,
            run_id=manifest.run_id,
            target_id=manifest.target_id,
            timeout=float("nan"),
        )

    #: aucun fichier stop n'a été déposé malgré l'appel refusé
    assert not (Path(manifest.session_dir) / session_mod.STOP_NAME).exists()


def test_start_session_bootstraps_and_returns_supervised_manifest(tmp_path, monkeypatch):
    """start_session délègue le lancement à un superviseur détaché via un
    fichier bootstrap privé, puis retourne le manifest que ce superviseur a
    écrit, avec le timeout demandé propagé tel quel."""
    monkeypatch.setattr(
        session_mod.secrets,
        "token_hex",
        lambda size: SESSION_ID if size == 12 else PROFILE_ID,
    )
    monkeypatch.setattr(session_mod, "find_chrome", lambda _explicit=None: "/fake/chrome")
    launched = []

    class FakeSupervisor:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(argv, **kwargs):
        launched.append((argv, kwargs))
        bootstrap_path = Path(argv[4])
        data = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        manifest = SessionManifest(
            session_id=data["session_id"],
            run_id=data["run_id"],
            profile_id=data["profile_id"],
            browser_kind=data["browser_kind"],
            authority=data["authority"],
            origins=tuple(data["origins"]),
            host="127.0.0.1",
            port=9333,
            target_id="TARGET",
            websocket_url="ws://127.0.0.1:9333/devtools/page/TARGET",
            browser_pid=os.getpid(),
            browser_start_time="linux:fake-browser",
            supervisor_pid=4242,
            supervisor_start_time="linux:fake-supervisor",
            owner_pid=data["owner_pid"],
            owner_start_time=data["owner_start_time"],
            session_dir=data["session_dir"],
            profile_dir=data["profile_dir"],
            artifacts_dir=data["artifacts_dir"],
            created_at=data["created_at"],
            expires_at=data["expires_at"],
        )
        write_manifest(manifest)
        return FakeSupervisor()

    monkeypatch.setattr(session_mod.subprocess, "Popen", fake_popen)

    manifest, path = start_session(
        run_id="run-start",
        authority="observation",
        origins="http://demo.test",
        owner_pid=os.getpid(),
        chrome_bin="ignored",
        root=tmp_path,
        timeout=1,
    )

    #: le manifest rendu à l'appelant est celui produit par le superviseur,
    #: avec la cible qu'il a assignée, au chemin canonique de la session
    assert manifest.target_id == "TARGET"
    assert path == tmp_path / SESSION_ID / "manifest.json"
    #: le superviseur est lancé comme module cdpx dans sa propre session de
    #: processus, condition de survie après la mort du parent
    assert launched[0][0][:4] == [session_mod.sys.executable, "-m", "cdpx.session", "_supervise"]
    assert launched[0][1]["start_new_session"] is True
    bootstrap = json.loads(Path(launched[0][0][4]).read_text(encoding="utf-8"))
    #: le timeout demandé arrive intact dans le bootstrap du superviseur
    assert bootstrap["startup_timeout"] == 1.0


def test_start_session_fails_closed_on_bootstrap_error_and_timeout(tmp_path, monkeypatch):
    """Un échec écrit par le superviseur pendant le bootstrap remonte tel quel
    à l'appelant, le superviseur est avorté et le fichier d'erreur consommé;
    un TTL nul est quant à lui rejeté avant tout lancement."""
    monkeypatch.setattr(
        session_mod.secrets,
        "token_hex",
        lambda size: SESSION_ID if size == 12 else PROFILE_ID,
    )
    monkeypatch.setattr(session_mod, "find_chrome", lambda _explicit=None: "/fake/chrome")
    aborted = []

    class FakeSupervisor:
        pid = 5151

        def poll(self):
            return None

    def error_popen(argv, **_kwargs):
        bootstrap = Path(argv[4])
        data = json.loads(bootstrap.read_text(encoding="utf-8"))
        (bootstrap.parent.parent / f"{data['session_id']}.error").write_text(
            "synthetic bootstrap failure",
            encoding="utf-8",
        )
        return FakeSupervisor()

    monkeypatch.setattr(session_mod.subprocess, "Popen", error_popen)
    monkeypatch.setattr(
        session_mod,
        "_abort_supervisor",
        lambda supervisor, path: aborted.append((supervisor.pid, path)),
    )
    #: le message déposé par le superviseur est relayé à l'appelant, qui sait
    #: donc pourquoi le démarrage a échoué
    with pytest.raises(PolicyError, match="synthetic bootstrap failure"):
        start_session(
            run_id="run-error",
            authority="observation",
            origins="http://demo.test",
            chrome_bin="ignored",
            root=tmp_path,
            timeout=1,
        )
    #: le superviseur fautif a été avorté et le fichier d'erreur consommé,
    #: donc pas de processus zombie ni de résidu sur disque
    assert aborted and not (tmp_path / f"{SESSION_ID}.error").exists()

    #: un TTL nul est refusé d'emblée, sans même tenter un lancement
    with pytest.raises(PolicyError, match="strictement positif"):
        start_session(
            run_id="run-timeout",
            authority="observation",
            origins="http://demo.test",
            ttl=0,
            chrome_bin="ignored",
            root=tmp_path,
        )


def test_start_session_timeout_reports_redacted_log_tails_before_cleanup(
    tmp_path,
    monkeypatch,
):
    """Quand la session n'est pas prête à temps, le diagnostic remonte la fin
    des logs superviseur et Chrome — la valeur secrète y est masquée — et le
    nettoyage n'intervient qu'après leur lecture."""
    monkeypatch.setattr(
        session_mod.secrets,
        "token_hex",
        lambda size: SESSION_ID if size == 12 else PROFILE_ID,
    )
    monkeypatch.setattr(session_mod, "find_chrome", lambda _explicit=None: "/fake/chrome")
    secret = "diagnostic-secret-value"
    monkeypatch.setenv("CI_SECRET_TOKEN", secret)

    class FakeSupervisor:
        pid = 5151

        def poll(self):
            return None

    def stalled_popen(argv, **_kwargs):
        session_dir = Path(argv[4]).parent
        (session_dir / "supervisor.log").write_text(
            f"startup_stage=wait_devtools\nAuthorization: Bearer {secret}\n",
            encoding="utf-8",
        )
        (session_dir / "chrome-stderr.log").write_text(
            f"Chrome could not start with token={secret}\n",
            encoding="utf-8",
        )
        (session_dir / "chrome-stderr.log").chmod(0o600)
        return FakeSupervisor()

    clock = iter((0.0, 0.0, 4.0))
    monkeypatch.setattr(session_mod.subprocess, "Popen", stalled_popen)
    monkeypatch.setattr(session_mod.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(session_mod.time, "sleep", lambda _seconds: None)

    cleanup_observation = {}

    def abort(supervisor, session_dir):
        cleanup_observation["pid"] = supervisor.pid
        cleanup_observation["logs_present"] = (session_dir / "supervisor.log").exists() and (
            session_dir / "chrome-stderr.log"
        ).exists()
        session_mod.shutil.rmtree(session_dir)

    monkeypatch.setattr(session_mod, "_abort_supervisor", abort)

    with pytest.raises(PolicyError) as caught:
        start_session(
            run_id="run-timeout",
            authority="observation",
            origins="http://demo.test",
            chrome_bin="ignored",
            root=tmp_path,
            timeout=1,
        )

    message = str(caught.value)
    #: le diagnostic nomme l'échec de readiness et cite les deux logs avec
    #: l'étape de démarrage atteinte, de quoi investiguer sans la session
    assert "session navigateur non prête" in message
    assert "supervisor.log" in message and "chrome-stderr.log" in message
    assert "startup_stage=wait_devtools" in message
    #: la valeur secrète issue de l'environnement n'atteint jamais le message,
    #: seul le marqueur de redaction y figure
    assert secret not in message
    assert "***" in message
    #: le nettoyage a bien eu lieu après lecture des logs, puis a tout retiré
    assert cleanup_observation == {"pid": 5151, "logs_present": True}
    assert not (tmp_path / SESSION_ID).exists()


def test_startup_diagnostics_refuse_symlinked_logs(tmp_path):
    """Les diagnostics de démarrage ne suivent pas les symlinks: un log
    pointant hors de la session est traité comme indisponible, jamais lu."""
    session_dir = tmp_path / "session"
    session_dir.mkdir(mode=0o700)
    outside = tmp_path / "outside.log"
    outside.write_text("must-not-be-read", encoding="utf-8")
    (session_dir / "supervisor.log").symlink_to(outside)

    diagnostics = session_mod._startup_diagnostic_tails(session_dir)

    #: le contenu du fichier extérieur n'a pas été exfiltré via le symlink
    assert "must-not-be-read" not in diagnostics
    #: le log détourné est présenté comme indisponible, pas comme une erreur
    assert "supervisor.log:\n<vide ou indisponible>" in diagnostics


@pytest.mark.parametrize(
    "overrides",
    [
        {"ttl": float("nan")},
        {"ttl": float("inf")},
        {"timeout": 0},
        {"timeout": float("nan")},
    ],
)
def test_start_session_rejects_non_finite_limits_before_creating_files(
    tmp_path,
    overrides,
):
    """TTL et timeout non finis ou nuls sont rejetés avant toute création de
    fichier: le runtime reste vierge quelle que soit la limite fautive."""
    #: chaque limite invalide déclenche le même refus de politique explicite
    with pytest.raises(PolicyError, match="fini et strictement positif"):
        start_session(
            run_id="run-invalid-limits",
            authority="observation",
            origins="http://demo.test",
            root=tmp_path,
            **overrides,
        )
    #: le refus précède la création du moindre fichier de session
    assert list(tmp_path.iterdir()) == []


def test_start_session_rejects_unbounded_startup_timeout(tmp_path):
    """Le timeout de démarrage est aussi borné par le haut: une attente
    au-delà du plafond est refusée avant tout effet sur le disque."""
    #: dépasser le plafond est un refus de politique, pas une attente infinie
    with pytest.raises(PolicyError, match="timeout de démarrage hors plage"):
        start_session(
            run_id="run-invalid-timeout",
            authority="observation",
            origins="http://demo.test",
            root=tmp_path,
            timeout=session_mod.MAX_STARTUP_TIMEOUT + 1,
        )
    #: rien n'a été créé sur disque avant le refus
    assert list(tmp_path.iterdir()) == []


def test_start_session_cleans_private_tree_when_supervisor_spawn_fails(tmp_path, monkeypatch):
    """Si le spawn du superviseur échoue, start_session propage l'erreur
    système d'origine mais supprime d'abord l'arborescence privée déjà
    créée pour le bootstrap."""
    monkeypatch.setattr(
        session_mod.secrets,
        "token_hex",
        lambda size: SESSION_ID if size == 12 else PROFILE_ID,
    )
    monkeypatch.setattr(session_mod, "find_chrome", lambda _explicit=None: "/fake/chrome")

    def fail_popen(*_args, **_kwargs):
        raise OSError("synthetic spawn failure")

    monkeypatch.setattr(session_mod.subprocess, "Popen", fail_popen)

    #: l'erreur système d'origine est propagée sans être avalée ni maquillée
    with pytest.raises(OSError, match="synthetic spawn failure"):
        start_session(
            run_id="run-spawn-failure",
            authority="observation",
            origins="http://demo.test",
            chrome_bin="ignored",
            root=tmp_path,
        )
    #: l'arborescence créée avant le spawn a été entièrement nettoyée
    assert not (tmp_path / SESSION_ID).exists()


def test_supervisor_builds_manifest_closes_extra_target_and_cleans_up(tmp_path, monkeypatch):
    """Le superviseur exige une attestation valide puis déroule le cycle
    complet: manifest écrit et rechargeable, targets surnuméraires fermés,
    Chrome terminé (puis tué s'il résiste) et session supprimée au SIGTERM."""
    session_dir = tmp_path / SESSION_ID
    profile_dir = session_dir / "profile"
    artifacts_dir = session_dir / "artifacts"
    for path in (session_dir, profile_dir, artifacts_dir):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    bootstrap = session_dir / "bootstrap.json"
    now = datetime.now(UTC)
    bootstrap.write_text(
        json.dumps(
            {
                "session_id": SESSION_ID,
                "run_id": "run-supervisor",
                "profile_id": PROFILE_ID,
                "browser_kind": "chrome",
                "authority": "interaction",
                "origins": ["http://demo.test"],
                "owner_pid": None,
                "owner_start_time": None,
                "chrome_bin": "/fake/chrome",
                "startup_timeout": 60.0,
                "session_dir": str(session_dir),
                "profile_dir": str(profile_dir),
                "artifacts_dir": str(artifacts_dir),
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    bootstrap.chmod(0o600)
    attestation = session_mod._policy_attestation(json.loads(bootstrap.read_text(encoding="utf-8")))
    handlers = {}
    monkeypatch.setattr(
        session_mod.signal,
        "signal",
        lambda signum, handler: handlers.setdefault(signum, handler),
    )

    class FakeChrome:
        pid = 6262
        killed = False
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            if timeout is not None and not self.killed:
                raise subprocess.TimeoutExpired("chrome", timeout)
            return 0

        def kill(self):
            self.killed = True

    chrome = FakeChrome()

    def fake_popen(*_args, **_kwargs):
        handlers[session_mod.signal.SIGTERM](session_mod.signal.SIGTERM, None)
        return chrome

    monkeypatch.setattr(session_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        session_mod,
        "_process_identity",
        lambda pid: (
            ("linux:chrome", (f"--user-data-dir={profile_dir}",))
            if pid == chrome.pid
            else (
                "linux:supervisor",
                (
                    "-m",
                    "cdpx.session",
                    "_supervise",
                    str(bootstrap),
                    f"--attestation={attestation}",
                ),
            )
        ),
    )
    monkeypatch.setattr(session_mod, "_read_devtools_port", lambda *_args, **_kwargs: 9444)
    monkeypatch.setattr(session_mod, "_wait_discovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        session_mod.discovery,
        "new_tab",
        lambda *_args: {
            "id": "ASSIGNED",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9444/devtools/page/ASSIGNED",
        },
    )
    target_lists = iter(
        (
            [
                {"id": "OLD", "type": "page"},
                {
                    "id": "ASSIGNED",
                    "type": "page",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9444/devtools/page/ASSIGNED",
                },
                {"id": "WORKER", "type": "worker"},
            ],
            [
                {
                    "id": "ASSIGNED",
                    "type": "page",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9444/devtools/page/ASSIGNED",
                }
            ],
            [
                {
                    "id": "ASSIGNED",
                    "type": "page",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9444/devtools/page/ASSIGNED",
                }
            ],
        )
    )
    monkeypatch.setattr(session_mod.discovery, "list_targets", lambda *_args: next(target_lists))
    closed = []
    monkeypatch.setattr(
        session_mod.discovery,
        "close_tab",
        lambda _host, _port, target: closed.append(target),
    )
    real_rmtree = session_mod.shutil.rmtree
    removed = []
    monkeypatch.setattr(
        session_mod.shutil,
        "rmtree",
        lambda path, ignore_errors=False: removed.append((Path(path), ignore_errors)),
    )

    #: une attestation invalide fait échouer le superviseur sans toucher au
    #: bootstrap ni au dossier de session
    assert session_mod._supervise(bootstrap, "0" * 64) == 1
    assert bootstrap.exists() and session_dir.exists()
    result = session_mod._supervise(bootstrap, attestation)

    #: avec la bonne attestation, le cycle supervisé se termine proprement
    assert result == 0
    manifest = load_manifest(session_dir / "manifest.json", run_id="run-supervisor")
    #: le manifest écrit est rechargeable pour ce run et pointe le target
    #: assigné sur le port réellement découvert
    assert manifest.target_id == "ASSIGNED" and manifest.port == 9444
    #: l'onglet initial superflu est fermé au démarrage, la cible assignée
    #: l'est à l'arrêt — le worker n'est jamais touché
    assert closed == ["OLD", "ASSIGNED"]
    #: Chrome reçoit terminate puis kill lorsqu'il ignore le délai d'arrêt
    assert chrome.terminated is True and chrome.killed is True
    #: la session est supprimée exactement une fois, sans erreurs masquées
    assert removed == [(session_dir, False)]
    real_rmtree(session_dir)


def test_supervisor_rejects_invalid_bootstrap_without_writing_or_cleanup(tmp_path):
    """Un bootstrap illisible fait échouer le superviseur sans publication de
    fichier d'erreur ni destruction: rien n'est nettoyé pour une entrée qui
    n'a pas prouvé être une session cdpx."""
    session_dir = tmp_path / SESSION_ID
    session_dir.mkdir(mode=0o700)
    bootstrap = session_dir / "bootstrap.json"
    bootstrap.write_text("not-json", encoding="utf-8")
    bootstrap.chmod(0o600)

    #: le superviseur refuse l'entrée non parsable avec un code d'échec
    assert session_mod._supervise(bootstrap, "0" * 64) == 1
    error = tmp_path / f"{SESSION_ID}.error"
    #: aucun fichier d'erreur n'est publié pour une entrée non attestée
    assert not error.exists()
    #: le dossier et son contenu restent exactement tels qu'avant l'appel
    assert session_dir.exists()
    assert bootstrap.read_text(encoding="utf-8") == "not-json"


def test_supervisor_error_preserves_redacted_readiness_tails(tmp_path, monkeypatch):
    """Quand la readiness échoue côté superviseur, le fichier d'erreur publié
    conserve la cause et les fins de logs — la valeur secrète y est masquée —
    puis la session est intégralement détruite."""
    session_dir = tmp_path / SESSION_ID
    profile_dir = session_dir / "profile"
    artifacts_dir = session_dir / "artifacts"
    for path in (session_dir, profile_dir, artifacts_dir):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    now = datetime.now(UTC)
    bootstrap = session_dir / "bootstrap.json"
    payload = {
        "session_id": SESSION_ID,
        "run_id": "run-readiness-error",
        "profile_id": PROFILE_ID,
        "browser_kind": "chrome",
        "authority": "observation",
        "origins": ["http://demo.test"],
        "owner_pid": None,
        "owner_start_time": None,
        "chrome_bin": "/fake/chrome",
        "startup_timeout": 60.0,
        "session_dir": str(session_dir),
        "profile_dir": str(profile_dir),
        "artifacts_dir": str(artifacts_dir),
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
    }
    bootstrap.write_text(json.dumps(payload), encoding="utf-8")
    bootstrap.chmod(0o600)
    attestation = session_mod._policy_attestation(payload)
    secret = "readiness-secret-value"
    monkeypatch.setenv("CI_SECRET_TOKEN", secret)
    monkeypatch.setattr(session_mod.signal, "signal", lambda *_args: None)

    class FakeChrome:
        pid = 6262

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    def fake_popen(*_args, **_kwargs):
        (session_dir / "chrome-stderr.log").write_text(
            f"cold start blocked token={secret}\n",
            encoding="utf-8",
        )
        return FakeChrome()

    monkeypatch.setattr(session_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        session_mod,
        "_read_devtools_port",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PolicyError("synthetic readiness timeout")),
    )

    #: l'échec de readiness fait sortir le superviseur en erreur
    assert session_mod._supervise(bootstrap, attestation) == 1

    error = tmp_path / f"{SESSION_ID}.error"
    message = error.read_text(encoding="utf-8")
    #: la cause d'origine et les extraits des deux logs survivent dans le
    #: fichier d'erreur, seul témoin après la destruction de la session
    assert "synthetic readiness timeout" in message
    assert "supervisor.log" in message and "chrome-stderr.log" in message
    #: la valeur secrète est redactée avant d'atteindre le fichier d'erreur
    assert secret not in message and "***" in message
    #: le dossier de session, lui, est bien nettoyé malgré l'échec
    assert not session_dir.exists()


def test_supervisor_arbitrary_path_never_removes_or_chmods_its_parent(tmp_path):
    """Pointer le superviseur vers un fichier quelconque d'un projet ne
    détruit ni ne re-chmod le dossier parent: l'échec est totalement sans
    effet de bord."""
    victim = tmp_path / "project"
    victim.mkdir(mode=0o755)
    keep = victim / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    arbitrary = victim / "README.md"
    arbitrary.write_text("not a bootstrap", encoding="utf-8")
    before_mode = stat.S_IMODE(victim.stat().st_mode)

    #: le superviseur refuse le fichier arbitraire avec un simple code d'échec
    assert session_mod._supervise(arbitrary, "0" * 64) == 1

    #: le dossier victime est intact: contenu préservé et permissions
    #: inchangées, aucun durcissement 0700 appliqué à un dossier étranger
    assert keep.read_text(encoding="utf-8") == "keep"
    assert arbitrary.exists()
    assert stat.S_IMODE(victim.stat().st_mode) == before_mode
    #: aucun fichier d'erreur n'est déposé à côté d'un dossier étranger
    assert not (tmp_path / "project.error").exists()


def test_single_target_enforcement_fails_closed_when_popup_cannot_close(
    tmp_path,
    monkeypatch,
):
    """Si un popup surnuméraire refuse de se fermer, la règle un-seul-target
    échoue-fermé au lieu de laisser la session continuer avec deux pages."""
    manifest = manifest_for(tmp_path)
    assigned = {
        "id": manifest.target_id,
        "type": "page",
        "webSocketDebuggerUrl": manifest.websocket_url,
    }
    popup = {
        "id": "POPUP",
        "type": "page",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/POPUP",
    }
    monkeypatch.setattr(
        session_mod.discovery,
        "list_targets",
        lambda *_args: [assigned, popup],
    )

    def refuse_close(*_args):
        raise session_mod.discovery.DiscoveryError("synthetic close refusal")

    monkeypatch.setattr(session_mod.discovery, "close_tab", refuse_close)

    #: le refus de fermeture devient une erreur de politique explicite, pas
    #: un silence qui laisserait une page non supervisée ouverte
    with pytest.raises(PolicyError, match="fermeture.*échouée"):
        session_mod._enforce_single_page_target(manifest)


def test_single_target_enforcement_waits_for_async_close(tmp_path, monkeypatch):
    """La fermeture d'un popup est asynchrone côté Chrome: l'enforcement
    attend, de manière bornée, que la liste des targets converge au lieu de
    re-fermer en boucle ou d'échouer trop tôt."""
    manifest = manifest_for(tmp_path)
    assigned = {
        "id": manifest.target_id,
        "type": "page",
        "webSocketDebuggerUrl": manifest.websocket_url,
    }
    popup = {
        "id": "POPUP",
        "type": "page",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/POPUP",
    }
    discoveries = iter(([assigned, popup], [assigned, popup], [assigned], [assigned]))
    monkeypatch.setattr(
        session_mod.discovery,
        "list_targets",
        lambda *_args: next(discoveries),
    )
    closed: list[str] = []
    monkeypatch.setattr(
        session_mod.discovery,
        "close_tab",
        lambda _host, _port, target_id: closed.append(target_id),
    )
    monkeypatch.setattr(session_mod.time, "sleep", lambda _seconds: None)

    session_mod._enforce_single_page_target(manifest, close_timeout=0.1)

    #: un seul ordre de fermeture est émis malgré plusieurs relevés où le
    #: popup traîne encore: l'attente remplace la ré-émission
    assert closed == ["POPUP"]


def test_exact_target_attestation_rejects_extra_page(tmp_path, monkeypatch):
    """L'attestation stricte du target échoue dès qu'une page supplémentaire
    coexiste avec la cible assignée: la session ne travaille jamais dans un
    navigateur partagé."""
    manifest = manifest_for(tmp_path)
    monkeypatch.setattr(
        session_mod.discovery,
        "list_targets",
        lambda *_args: [
            {
                "id": manifest.target_id,
                "type": "page",
                "webSocketDebuggerUrl": manifest.websocket_url,
            },
            {
                "id": "POPUP",
                "type": "page",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/POPUP",
            },
        ],
    )

    #: la présence d'une deuxième page viole le contrat d'exclusivité de la
    #: cible et invalide l'attestation
    with pytest.raises(PolicyError, match="un seul target page"):
        session_mod._assert_exact_target(manifest)


def test_session_status_activity_runtime_root_and_chrome_discovery(tmp_path, monkeypatch):
    """session_status reflète l'état réel des processus et
    assert_session_active vérifie chaque lien de la chaîne (port lié,
    marqueurs, identité des PID, expiration) en échouant-fermé sur toute
    dérive; runtime_root et find_chrome complètent la découverte locale."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    status = session_status(path, run_id=manifest.run_id, target_id=manifest.target_id)
    #: sur des PID morts, le statut rapporte navigateur et superviseur
    #: arrêtés sans lever d'erreur
    assert status["browser_running"] is False and status["supervisor_running"] is False

    active = replace(
        manifest,
        browser_pid=os.getpid(),
        browser_start_time="active-start",
        supervisor_pid=os.getpid(),
        supervisor_start_time="active-start",
        owner_start_time="active-start",
        expires_at=(datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    )
    markers = (
        f"--user-data-dir={active.profile_dir}",
        *session_mod._supervisor_markers(active),
    )

    def process_identity(pid):
        if pid != os.getpid():
            return "wrong-start", ("unrelated",)
        return "active-start", markers

    monkeypatch.setattr(session_mod, "_process_identity", process_identity)
    monkeypatch.setattr(
        session_mod.discovery,
        "list_targets",
        lambda *_args: [
            {
                "id": active.target_id,
                "type": "page",
                "webSocketDebuggerUrl": active.websocket_url,
            }
        ],
    )
    (Path(active.profile_dir) / "DevToolsActivePort").write_text(
        f"{active.port}\n/devtools/browser/id\n",
        encoding="utf-8",
    )
    #: la session cohérente (PID vivants, marqueurs, port lié, non expirée)
    #: est attestée active sans erreur
    assert_session_active(active)
    active_port = Path(active.profile_dir) / "DevToolsActivePort"
    active_port.write_text("1\n/devtools/browser/id\n", encoding="utf-8")
    #: un DevToolsActivePort divergent prouve que le profil n'est plus servi
    #: par le port du manifest
    with pytest.raises(PolicyError, match="non lié au port"):
        assert_session_active(active)
    active_port.write_text(f"{active.port}\n/devtools/browser/id\n", encoding="utf-8")
    #: un start_time différent trahit un PID recyclé par un autre processus
    with pytest.raises(PolicyError, match="réutilisé"):
        assert_session_active(replace(active, browser_start_time="stale-start"))
    #: un profil déplacé ne porte plus le marqueur user-data-dir attendu
    with pytest.raises(PolicyError, match="marqueur"):
        assert_session_active(
            replace(active, profile_dir=str(Path(active.session_dir) / "other-profile"))
        )
    #: modifier l'autorité casse les marqueurs attestés du superviseur
    with pytest.raises(PolicyError, match="supervisor.*marqueur"):
        assert_session_active(replace(active, authority="privileged"))
    #: une expiration illisible est un refus, jamais une session éternelle
    with pytest.raises(PolicyError, match="expires_at"):
        assert_session_active(replace(active, expires_at="invalid"))
    #: une session expirée est refusée même si tous les processus tournent
    with pytest.raises(PolicyError, match="expirée"):
        assert_session_active(
            replace(active, expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
        )
    #: la disparition du navigateur ou du superviseur suffit, chacune, à
    #: invalider la session
    with pytest.raises(PolicyError, match="navigateur"):
        assert_session_active(replace(active, browser_pid=999_999))
    with pytest.raises(PolicyError, match="supervisor"):
        assert_session_active(replace(active, supervisor_pid=999_998))

    runtime = tmp_path / "runtime"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    #: la racine runtime suit XDG_RUNTIME_DIR, isolée par utilisateur
    assert runtime_root() == runtime / "cdpx"
    executable = tmp_path / "chromium"
    executable.write_text("binary", encoding="utf-8")
    #: un binaire explicite existant est accepté tel quel, un chemin
    #: manquant est une erreur de politique et non un fallback silencieux
    assert find_chrome(str(executable)) == str(executable)
    with pytest.raises(PolicyError, match="introuvable"):
        find_chrome(str(tmp_path / "missing"))


def test_devtools_port_and_discovery_readiness_are_bounded(tmp_path, monkeypatch):
    """La lecture de DevToolsActivePort et l'attente de la discovery sont
    bornées: succès immédiat quand le port est valide, timeout net sinon, et
    arrêt anticipé dès que le processus Chrome est déjà mort."""
    profile = tmp_path / "profile"
    profile.mkdir()

    class Running:
        returncode = None

        def poll(self):
            return None

    class Stopped:
        returncode = 7

        def poll(self):
            return 7

    (profile / "DevToolsActivePort").write_text("9555\n/devtools/browser/id\n", encoding="utf-8")
    #: un fichier de port valide est lu dès le premier passage, sans attente
    assert session_mod._read_devtools_port(profile, Running(), timeout=1) == 9555

    (profile / "DevToolsActivePort").write_text("invalid\n", encoding="utf-8")
    ticks = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(session_mod.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(session_mod.time, "sleep", lambda _delay: None)
    #: un contenu invalide épuise le timeout sans jamais retourner de port
    with pytest.raises(PolicyError, match="introuvable"):
        session_mod._read_devtools_port(profile, Running(), timeout=0.5)
    ticks = iter((0.0, 0.0))
    #: un Chrome déjà terminé court-circuite l'attente au lieu de la vider
    with pytest.raises(PolicyError, match="readiness"):
        session_mod._read_devtools_port(profile, Stopped(), timeout=1)

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Opener:
        def open(self, url, timeout):
            assert url == "http://127.0.0.1:9555/json/version"
            assert timeout == 0.5
            return Response()

    monkeypatch.setattr(
        session_mod.urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )
    ticks = iter((0.0, 0.0))
    monkeypatch.setattr(session_mod.time, "monotonic", lambda: next(ticks))
    #: la discovery prête (HTTP 200 sur l'endpoint loopback attendu, vérifié
    #: par le faux opener) rend la main sans erreur
    session_mod._wait_discovery(9555, Running(), timeout=1)

    ticks = iter((0.0, 0.0))
    monkeypatch.setattr(session_mod.time, "monotonic", lambda: next(ticks))
    #: un processus mort pendant l'attente de discovery est un échec immédiat
    with pytest.raises(PolicyError, match="discovery"):
        session_mod._wait_discovery(9555, Stopped(), timeout=1)
