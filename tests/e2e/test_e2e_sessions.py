"""E2E Chrome réel du lifecycle et de l'isolation des sessions supervisées."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cdpx import discovery
from cdpx.client import CDPClient
from cdpx.primitives import capture, state
from cdpx.session import (
    SessionLease,
    SessionManifest,
    find_chrome,
    load_manifest,
    stop_session,
)
from cdpx.testing.e2e import attach_cli_run


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


def start_managed_session(
    *,
    run_id: str,
    authority: str,
    origins: str,
    chrome_bin: str,
    runtime_dir: Path,
    evidence_case=None,
    start_label: str | None = None,
) -> tuple[SessionManifest, Path]:
    proc = run_session_cli(
        "start",
        "--run-id",
        run_id,
        "--authority",
        authority,
        "--origins",
        origins,
        "--ttl",
        "300",
        "--owner-pid",
        str(os.getpid()),
        "--chrome",
        chrome_bin,
        env={"XDG_RUNTIME_DIR": str(runtime_dir)},
    )
    assert proc.returncode == 0 and not proc.stderr, (
        f"session start en échec: exit={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    if start_label is not None:
        attach_cli_run(evidence_case, start_label, proc)
    payload = json.loads(proc.stdout)
    assert payload["started"] is True
    path = Path(payload["manifest"])
    manifest = load_manifest(path, run_id=run_id, target_id=payload["target_id"])
    return manifest, path


def run_browser_cli(
    manifest: SessionManifest,
    manifest_path: Path,
    *args: str,
    timeout: float = 20,
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
            "15",
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )


def successful_session_json(
    manifest: SessionManifest,
    manifest_path: Path,
    *args: str,
) -> dict:
    proc = run_browser_cli(manifest, manifest_path, *args)
    assert proc.returncode == 0 and not proc.stderr, (
        f"CLI de session en échec: exit={proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict)
    assert payload["_cdpx"] == {
        "run_id": manifest.run_id,
        "session_id": manifest.session_id,
        "target_id": manifest.target_id,
        "authority": manifest.authority,
        "content_trust": "untrusted",
    }
    return payload


def browser_state(manifest: SessionManifest) -> tuple[dict, dict]:
    with CDPClient(manifest.websocket_url, timeout=10) as client:
        cookies = state.get_cookies(client, show_values=True)
        local_storage = state.get_storage(client, kind="local", show_values=True)
    return cookies, local_storage


def attach_session_screenshot(
    evidence_case, manifest: SessionManifest, path: Path, label: str
) -> None:
    if evidence_case is None:
        return
    with CDPClient(manifest.websocket_url, timeout=10) as client:
        capture.screenshot(client, str(path))
    evidence_case.attach_file(path, label, "screenshot")


def port_is_closed(port: int, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                pass
        except OSError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.scenario(
    feature="state-session",
    journey="isolate-session-runs",
    scenario_id="state-session.isolate-supervised-session-runs",
    proves=[
        "Managed runs use distinct Chrome profiles, targets and loopback endpoints.",
        "Cookies and localStorage do not cross session boundaries.",
        "Authority grants and the exclusive command lease are enforced by the real CLI.",
        "A popup target is closed by the supervisor before it can persist in the run.",
        "Stopping a run removes its private files and closes its CDP endpoint.",
    ],
)
def test_supervised_sessions_are_isolated_authorized_and_torn_down(
    fixtures_http,
    tmp_path,
    evidence_case,
):
    """Trois runs supervisés simultanés vivent dans des Chrome réellement
    étanches (profils, cibles, ports, cookies, storage), chaque autorité borne
    ce que le CLI accepte, et l'arrêt ne laisse ni fichier ni port ouvert."""
    chrome_bin = find_chrome()
    runtime_dir = tmp_path / "runtime"
    origins = fixtures_http.base_url
    sessions: list[tuple[SessionManifest, Path]] = []
    proof: dict = {
        "sessions": [],
        "status": {},
        "isolation": {},
        "authority": {},
        "lease": {},
        "teardown": [],
    }

    try:
        for run_id, authority in (
            ("e2e-observation", "observation"),
            ("e2e-interaction", "interaction"),
            ("e2e-privileged", "privileged"),
        ):
            manifest, path = start_managed_session(
                run_id=run_id,
                authority=authority,
                origins=origins,
                chrome_bin=chrome_bin,
                runtime_dir=runtime_dir,
            )
            sessions.append((manifest, path))
            proof["sessions"].append(manifest.public_dict())

        observation, interaction, privileged = sessions
        manifests = [item[0] for item in sessions]
        #: chaque run reçoit son identité, son profil disque, sa cible et son
        #: port loopback propres: aucune ressource n'est partagée entre runs
        assert len({item.session_id for item in manifests}) == 3
        assert len({item.profile_id for item in manifests}) == 3
        assert len({item.profile_dir for item in manifests}) == 3
        assert len({item.target_id for item in manifests}) == 3
        assert len({item.port for item in manifests}) == 3
        assert all(Path(item.profile_dir).is_dir() for item in manifests)

        status_proc = run_session_cli(
            "status",
            "--session",
            str(observation[1]),
            "--run-id",
            observation[0].run_id,
            "--target",
            observation[0].target_id,
            env={"XDG_RUNTIME_DIR": str(runtime_dir)},
        )
        #: la commande status répond proprement sur stdout seul
        assert status_proc.returncode == 0 and not status_proc.stderr
        status = json.loads(status_proc.stdout)
        #: navigateur et superviseur sont déclarés vivants, mais le statut ne
        #: divulgue ni l'endpoint WebSocket ni le chemin du profil privé
        assert status["browser_running"] is True
        assert status["supervisor_running"] is True
        assert "websocket_url" not in status and "profile_dir" not in status
        proof["status"] = status

        observation_url = f"{fixtures_http.base_url}/storage.html"
        interaction_url = f"{fixtures_http.base_url}/form.html"
        privileged_url = f"{fixtures_http.base_url}/index.html"
        #: chaque session navigue vers sa propre page témoin via le CLI réel,
        #: enveloppe _cdpx vérifiée par le helper au passage
        assert successful_session_json(*observation, "goto", observation_url)["ok"] is True
        assert successful_session_json(*interaction, "goto", interaction_url)["ok"] is True
        assert successful_session_json(*privileged, "goto", privileged_url)["ok"] is True

        assigned_urls = {
            item.run_id: discovery.pick_page(item.host, item.port, item.target_id)["url"]
            for item in manifests
        }
        #: interrogé endpoint par endpoint, chaque Chrome affiche l'URL de son
        #: run et aucune autre: les navigations ne se sont pas croisées
        assert assigned_urls == {
            observation[0].run_id: observation_url,
            interaction[0].run_id: interaction_url,
            privileged[0].run_id: privileged_url,
        }
        attach_session_screenshot(
            evidence_case,
            observation[0],
            tmp_path / "managed-session.png",
            "Managed session target",
        )

        observation_cookies, observation_storage = browser_state(observation[0])
        interaction_cookies, interaction_storage = browser_state(interaction[0])
        observation_cookie_names = {item["name"] for item in observation_cookies["cookies"]}
        interaction_cookie_names = {item["name"] for item in interaction_cookies["cookies"]}
        #: le cookie et la clé localStorage créés par la page de la session
        #: observation existent chez elle et n'ont jamais fui vers la session
        #: interaction: l'état navigateur est cloisonné par profil
        assert "jsCookie" in observation_cookie_names
        assert observation_storage["entries"]["cdpx-key"] == "cdpx-value"
        assert "jsCookie" not in interaction_cookie_names
        assert "cdpx-key" not in interaction_storage["entries"]
        proof["isolation"] = {
            "observation_cookie_names": sorted(observation_cookie_names),
            "interaction_cookie_names": sorted(interaction_cookie_names),
            "observation_local_storage_keys": sorted(observation_storage["entries"]),
            "interaction_local_storage_keys": sorted(interaction_storage["entries"]),
            "assigned_urls": assigned_urls,
        }

        observed = successful_session_json(*observation, "text", "h1")
        #: l'autorité observation suffit pour lire le DOM
        assert observed["text"] == "Storage"
        denied_interaction = run_browser_cli(*observation, "click", "h1")
        #: le clic est refusé en exit 1 avec un diagnostic qui nomme
        #: l'autorité manquante — le refus est explicable, pas muet
        assert denied_interaction.returncode == 1
        assert "requiert interaction" in denied_interaction.stderr
        attach_cli_run(evidence_case, "Denied click (observation authority)", denied_interaction)

        clicked = successful_session_json(*interaction, "click", "#submit-btn")
        #: l'autorité interaction permet un vrai clic et le DOM en atteste
        #: par le résultat de soumission du formulaire
        assert clicked["clicked"] == "#submit-btn"
        assert successful_session_json(*interaction, "text", "#result")["text"] == "OK:"
        denied_privileged = run_browser_cli(*interaction, "eval", "document.title")
        #: l'évaluation JS arbitraire reste hors de portée d'interaction:
        #: refus explicite qui nomme le niveau privileged requis
        assert denied_privileged.returncode == 1
        assert "requiert privileged" in denied_privileged.stderr
        attach_cli_run(evidence_case, "Denied eval (interaction authority)", denied_privileged)

        evaluated = successful_session_json(*privileged, "eval", "document.title")
        #: l'autorité privileged débloque l'évaluation JS dans la vraie page
        assert evaluated["value"] == "cdpx fixtures — accueil"
        opened = successful_session_json(
            *privileged,
            "eval",
            "window.open('about:blank', '_blank'); true",
        )
        #: la page a bien exécuté l'ouverture du popup — le danger est réel
        assert opened["value"] is True
        popup_deadline = time.monotonic() + 5
        pages = []
        while time.monotonic() < popup_deadline:
            pages = [
                target
                for target in discovery.list_targets(privileged[0].host, privileged[0].port)
                if target.get("type") == "page"
            ]
            if [target.get("id") for target in pages] == [privileged[0].target_id]:
                break
            time.sleep(0.05)
        #: le superviseur a refermé le popup: seule la cible assignée au run
        #: survit, aucune fenêtre parasite ne peut s'installer
        assert [target.get("id") for target in pages] == [privileged[0].target_id]
        proof["authority"] = {
            "observation_text": "allowed",
            "observation_click": "denied",
            "interaction_click": "allowed",
            "interaction_eval": "denied",
            "privileged_eval": "allowed",
            "popup_target": "closed_by_supervisor",
        }

        with SessionLease(
            observation[1],
            run_id=observation[0].run_id,
            target_id=observation[0].target_id,
        ):
            contended = run_browser_cli(*observation, "text", "h1")
        #: tant que le bail exclusif est détenu ailleurs, la commande est
        #: refusée avec un diagnostic qui explique la contention
        assert contended.returncode == 1
        assert "session déjà utilisée" in contended.stderr
        attach_cli_run(evidence_case, "Contended command (lease held elsewhere)", contended)
        #: le bail relâché, la même commande repasse aussitôt: le refus
        #: venait bien du verrou, pas d'un état cassé
        assert successful_session_json(*observation, "text", "h1")["text"] == "Storage"
        proof["lease"] = {"while_held": "denied", "after_release": "allowed"}

        for manifest, path in reversed(sessions):
            profile_dir = Path(manifest.profile_dir)
            session_dir = Path(manifest.session_dir)
            stopped = run_session_cli(
                "stop",
                "--session",
                str(path),
                "--run-id",
                manifest.run_id,
                "--target",
                manifest.target_id,
                timeout=45,
                env={"XDG_RUNTIME_DIR": str(runtime_dir)},
            )
            #: l'arrêt de chaque run répond proprement, même exécuté en série
            #: pendant que d'autres sessions vivent encore
            assert stopped.returncode == 0 and not stopped.stderr, (
                f"session stop en échec: exit={stopped.returncode}\n"
                f"stdout={stopped.stdout}\nstderr={stopped.stderr}"
            )
            result = json.loads(stopped.stdout)
            closed = port_is_closed(manifest.port)
            teardown = {
                **result,
                "manifest_removed": not path.exists(),
                "profile_removed": not profile_dir.exists(),
                "session_dir_removed": not session_dir.exists(),
                "port_closed": closed,
            }
            proof["teardown"].append(teardown)
            #: l'arrêt ne laisse aucune trace: manifest, profil et répertoire
            #: privés supprimés, endpoint CDP loopback fermé
            assert all(
                teardown[key]
                for key in (
                    "stopped",
                    "manifest_removed",
                    "profile_removed",
                    "session_dir_removed",
                    "port_closed",
                )
            )
    finally:
        for manifest, path in reversed(sessions):
            if path.exists():
                with contextlib.suppress(Exception):
                    stop_session(
                        path,
                        run_id=manifest.run_id,
                        target_id=manifest.target_id,
                        timeout=10,
                    )
        if evidence_case is not None:
            evidence_case.attach_json(
                "Supervised session isolation",
                proof,
                "managed-session-isolation.json",
            )


@pytest.mark.scenario(
    feature="state-session",
    journey="teardown-supervisor-signal",
    scenario_id="state-session.teardown-on-supervisor-signal",
    proves=[
        "A normal supervisor termination closes Chrome and removes its ephemeral profile.",
        "The assigned CDP loopback endpoint is closed after teardown.",
    ],
)
def test_supervisor_signal_still_tears_down_chrome_and_private_files(
    fixtures_http,
    tmp_path,
    evidence_case,
):
    """Un simple SIGTERM au superviseur suffit à tout démanteler — Chrome
    fermé, fichiers privés supprimés, endpoint CDP clos — sans jamais passer
    par la commande stop du CLI."""
    chrome_bin = find_chrome()
    runtime_dir = tmp_path / "runtime"
    manifest, path = start_managed_session(
        run_id="e2e-signal-teardown",
        authority="observation",
        origins=fixtures_http.base_url,
        chrome_bin=chrome_bin,
        runtime_dir=runtime_dir,
        evidence_case=evidence_case,
        start_label="Session start (before SIGTERM)",
    )
    session_dir = Path(manifest.session_dir)
    profile_dir = Path(manifest.profile_dir)
    proof = {"session": manifest.public_dict()}
    try:
        #: la session est vivante et pilotable avant l'envoi du signal: le
        #: démantèlement observé ensuite ne peut pas être un faux positif
        assert (
            successful_session_json(
                manifest,
                path,
                "goto",
                f"{fixtures_http.base_url}/index.html",
            )["ok"]
            is True
        )
        attach_session_screenshot(
            evidence_case,
            manifest,
            tmp_path / "signal-teardown-session.png",
            "Session before supervisor teardown",
        )
        if evidence_case is not None:
            status_proc = run_session_cli(
                "status",
                "--session",
                str(path),
                "--run-id",
                manifest.run_id,
                "--target",
                manifest.target_id,
                env={"XDG_RUNTIME_DIR": str(runtime_dir)},
            )
            attach_cli_run(evidence_case, "Session status (alive, before SIGTERM)", status_proc)
        #: le manifest expose le pid du superviseur, seul destinataire du
        #: signal de terminaison
        assert manifest.supervisor_pid is not None
        os.kill(manifest.supervisor_pid, signal.SIGTERM)
        # Budget aligné sur le pire cas du teardown superviseur lui-même:
        # close_tab HTTP + terminate (5s) + kill (5s) + rmtree du profil —
        # 10s suffisaient à vide mais flakaient sous la charge de make proof.
        deadline = time.monotonic() + 30
        while session_dir.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        proof["teardown"] = {
            "manifest_removed": not path.exists(),
            "profile_removed": not profile_dir.exists(),
            "session_dir_removed": not session_dir.exists(),
            "port_closed": port_is_closed(manifest.port),
        }
        #: après le signal, plus aucune trace: manifest, profil et répertoire
        #: de session supprimés, port CDP fermé — le nettoyage est intrinsèque
        #: au superviseur, pas à la commande stop
        assert all(proof["teardown"].values())
    finally:
        if path.exists():
            with contextlib.suppress(Exception):
                stop_session(
                    path,
                    run_id=manifest.run_id,
                    target_id=manifest.target_id,
                    timeout=10,
                )
        if evidence_case is not None:
            evidence_case.attach_json(
                "Supervisor signal teardown",
                proof,
                "supervisor-signal-teardown.json",
            )
