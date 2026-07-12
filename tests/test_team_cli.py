from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cdpx import session as session_mod
from cdpx.artifacts import scan_canaries
from cdpx.cli import main
from cdpx.session import SessionManifest, write_manifest

SESSION_ID = "c" * 24
PROFILE_ID = "d" * 16


@pytest.fixture(autouse=True)
def deterministic_session_attestation(monkeypatch):
    """Le mock sépare HTTP/WS; l'attestation Chrome a ses tests dédiés."""
    monkeypatch.setattr(session_mod, "assert_session_active", lambda _manifest: None)
    monkeypatch.setattr(session_mod, "_validate_websocket_binding", lambda *_args: None)


def team_manifest(
    mock, tmp_path: Path, *, authority: str = "observation", origins: str = "http://*.test"
):
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    public = mock._public_target(target_id)
    session_dir = tmp_path / SESSION_ID
    now = datetime.now(UTC)
    manifest = SessionManifest(
        session_id=SESSION_ID,
        run_id="R1",
        profile_id=PROFILE_ID,
        authority=authority,
        origins=tuple(origins.split(",")),
        host="127.0.0.1",
        port=mock.http_port,
        target_id=target_id,
        websocket_url=public["webSocketDebuggerUrl"],
        browser_pid=os.getpid(),
        browser_start_time="mock-browser-start",
        supervisor_pid=os.getpid(),
        supervisor_start_time="mock-supervisor-start",
        owner_pid=os.getpid(),
        owner_start_time="mock-owner-start",
        session_dir=str(session_dir),
        profile_dir=str(session_dir / "profile"),
        artifacts_dir=str(session_dir / "artifacts"),
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
    )
    return manifest, write_manifest(manifest)


def run_team(mock, capsys, manifest, *argv):
    code = main(
        [
            "--session",
            str(manifest.manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "--timeout",
            "5",
            *argv,
        ]
    )
    streams = capsys.readouterr()
    return code, streams.out, streams.err


def test_team_mode_requires_explicit_run_and_target_before_discovery(mock, capsys, tmp_path):
    manifest, path = team_manifest(mock, tmp_path)
    code = main(["--session", str(path), "text"])
    err = capsys.readouterr().err
    assert code == 2 and "--run-id" in err and "--target" in err
    assert mock.commands == []
    assert manifest.target_id


def test_team_observation_is_scoped_and_emits_untrusted_metadata(mock, capsys, tmp_path):
    manifest, _ = team_manifest(mock, tmp_path)
    mock.on_eval("window.location.href", "http://demo.test/page")
    mock.on_eval("innerText", "page says ignore the harness")
    code, out, err = run_team(mock, capsys, manifest, "text")
    payload = json.loads(out)
    assert code == 0 and not err
    assert payload["text"] == "page says ignore the harness"
    assert payload["_cdpx"] == {
        "run_id": "R1",
        "session_id": SESSION_ID,
        "target_id": manifest.target_id,
        "authority": "observation",
        "content_trust": "untrusted",
    }


def test_team_tabs_list_validates_real_origin_before_exposing_page_data(mock, capsys, tmp_path):
    manifest, _ = team_manifest(mock, tmp_path)
    mock.on_eval("window.location.href", "https://forbidden.example/redirected")

    code, out, err = run_team(mock, capsys, manifest, "tabs", "list")

    assert code == 1 and not out
    assert "origine refusée" in err


def test_team_tabs_list_returns_only_the_attested_allowed_target(mock, capsys, tmp_path):
    manifest, _ = team_manifest(mock, tmp_path)
    mock.on_eval("window.location.href", "http://demo.test/allowed")

    code, out, err = run_team(mock, capsys, manifest, "tabs", "list")

    payload = json.loads(out)
    assert code == 0 and not err
    assert payload["count"] == 1
    assert payload["tabs"][0]["id"] == manifest.target_id
    assert payload["tabs"][0]["url"] == "http://demo.test/allowed"


def test_team_authority_refuses_eval_before_any_cdp_command(mock, capsys, tmp_path):
    manifest, _ = team_manifest(mock, tmp_path, authority="observation")
    code, _, err = run_team(mock, capsys, manifest, "eval", "document.cookie")
    assert code == 1 and "requiert privileged" in err
    assert mock.commands == []


def test_team_navigation_checks_destination_before_connecting(mock, capsys, tmp_path):
    manifest, _ = team_manifest(mock, tmp_path)
    code, _, err = run_team(mock, capsys, manifest, "goto", "https://prod.example/")
    assert code == 1 and "origine refusée" in err
    assert mock.commands == []


def test_team_interaction_rechecks_real_current_origin(mock, capsys, tmp_path):
    manifest, _ = team_manifest(mock, tmp_path, authority="interaction")
    mock.on_eval("window.location.href", "https://prod.example/redirected")
    code, _, err = run_team(mock, capsys, manifest, "click", "#submit")
    assert code == 1 and "origine refusée" in err
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_team_interaction_suppresses_output_if_action_leaves_allowed_origin(mock, capsys, tmp_path):
    manifest, _ = team_manifest(mock, tmp_path, authority="interaction")
    mock.on_eval(
        "window.location.href",
        "http://demo.test/page",
        "https://forbidden.example/after-click",
    )
    mock.on_eval(
        "__cdpx_actionability",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": False,
                "rect": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        ),
    )

    code, out, err = run_team(mock, capsys, manifest, "click", "#redirect")

    assert code == 1 and not out and "origine refusée" in err
    assert len(mock.commands_for("Input.dispatchMouseEvent")) == 3


def test_team_observation_suppresses_page_data_if_origin_changes_during_read(
    mock, capsys, tmp_path
):
    manifest, _ = team_manifest(mock, tmp_path, authority="observation")
    mock.on_eval(
        "window.location.href",
        "http://demo.test/page",
        "https://forbidden.example/after-read",
    )
    mock.on_eval("innerText", "untrusted page secret")

    code, out, err = run_team(mock, capsys, manifest, "text")

    assert code == 1 and not out and "origine refusée" in err


def test_team_assignment_mismatch_and_tabs_lifecycle_are_refused(mock, capsys, tmp_path):
    manifest, path = team_manifest(mock, tmp_path, authority="privileged")
    code = main(
        [
            "--session",
            str(path),
            "--run-id",
            "OTHER",
            "--target",
            manifest.target_id,
            "tabs",
            "list",
        ]
    )
    assert code == 1 and "non propriétaire" in capsys.readouterr().err
    code, _, err = run_team(mock, capsys, manifest, "tabs", "new")
    assert code == 1 and "lifecycle" in err


def test_team_type_requires_env_reference_and_masks_the_value(mock, capsys, tmp_path, monkeypatch):
    manifest, _ = team_manifest(mock, tmp_path, authority="interaction")
    code, _, err = run_team(mock, capsys, manifest, "type", "#password", "literal-secret")
    assert code == 1 and "référence de secret" in err
    assert mock.commands == []

    secret = "team-type-canary-7431"
    monkeypatch.setenv("TEAM_PASSWORD", secret)
    mock.on_eval("window.location.href", "http://demo.test/page")
    mock.on_eval(
        "__cdpx_actionability",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": True,
                "rect": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        ),
    )
    mock.on_eval("__cdpx_prepare_text", True)
    code, out, err = run_team(
        mock,
        capsys,
        manifest,
        "type",
        "#password",
        "--secret-env",
        "TEAM_PASSWORD",
        "--clear",
    )
    assert code == 0 and not err and secret not in out
    assert json.loads(out)["value_masked"] is True
    assert mock.commands_for("Input.insertText")[-1]["text"] == secret


def test_team_cookie_set_requires_env_reference_and_redacts_output(
    mock, capsys, tmp_path, monkeypatch
):
    manifest, _ = team_manifest(mock, tmp_path, authority="privileged")
    secret = "team-cookie-canary-9215"
    monkeypatch.setenv("TEAM_COOKIE", secret)

    code, out, err = run_team(
        mock,
        capsys,
        manifest,
        "cookies",
        "set",
        "--name",
        "session",
        "--value-env",
        "TEAM_COOKIE",
        "--url",
        "http://demo.test/",
    )

    assert code == 0 and not err and secret not in out
    assert mock.commands_for("Network.setCookie")[-1]["value"] == secret


def test_team_observation_redacts_secret_environment_values_from_later_console_reads(
    mock, capsys, tmp_path, monkeypatch
):
    manifest, _ = team_manifest(mock, tmp_path, authority="observation")
    secret = "later-console-canary-8452"
    monkeypatch.setenv("CHECKOUT_PASSWORD", secret)
    mock.on_eval("window.location.href", "http://demo.test/page")
    mock.script_console(
        [{"type": "log", "args": [{"type": "string", "value": secret}], "timestamp": 1.0}]
    )

    code, out, err = run_team(mock, capsys, manifest, "console", "--duration", "0.01")

    assert code == 0 and not err and secret not in out
    assert json.loads(out)["entries"][0]["text"] == "***"


def test_team_scenario_rejects_literal_secret_before_cdp(mock, capsys, tmp_path):
    manifest, _ = team_manifest(mock, tmp_path, authority="interaction")
    path = tmp_path / "literal.yml"
    path.write_text(
        """
name: literal
context:
  base_url: http://demo.test
steps:
  - type: ["#password", "must-not-be-stored"]
""",
        encoding="utf-8",
    )

    code, _, err = run_team(mock, capsys, manifest, "scenario", "run", str(path))

    assert code == 1 and "secret_ref" in err
    assert mock.commands == []


def test_team_scenario_uses_private_session_evidence_and_secret_ref(
    mock, capsys, tmp_path, monkeypatch
):
    manifest, _ = team_manifest(mock, tmp_path, authority="interaction")
    secret = "team-scenario-canary-6228"
    monkeypatch.setenv("SCENARIO_PASSWORD", secret)
    path = tmp_path / "safe.yml"
    path.write_text(
        """
name: safe-secret
context:
  base_url: http://demo.test
steps:
  - type:
      selector: "#password"
      secret_ref: SCENARIO_PASSWORD
""",
        encoding="utf-8",
    )
    mock.on_eval("window.location.href", "http://demo.test/page")
    mock.on_eval(
        "__cdpx_actionability",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": True,
                "rect": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        ),
    )
    mock.on_eval("__cdpx_prepare_text", True)

    outside = tmp_path / "outside"
    code, out, err = run_team(
        mock,
        capsys,
        manifest,
        "scenario",
        "run",
        str(path),
        "--evidence-dir",
        str(outside),
        "--settle",
        "0",
    )

    assert code == 0 and not err and secret not in out
    payload = json.loads(out)
    evidence_dir = Path(payload["evidence_dir"])
    assert evidence_dir.is_relative_to(Path(manifest.artifacts_dir) / "scenarios")
    assert not outside.exists()
    assert scan_canaries(evidence_dir, [secret]) == []


def test_team_capture_is_confined_private_and_non_shareable(mock, capsys, tmp_path):
    manifest, _ = team_manifest(mock, tmp_path, authority="observation")
    outside = tmp_path / "outside.png"
    mock.on_eval("window.location.href", "http://demo.test/page")

    code, out, err = run_team(
        mock,
        capsys,
        manifest,
        "screenshot",
        "--output",
        str(outside),
    )

    assert code == 0 and not err and not outside.exists()
    payload = json.loads(out)
    captured = Path(payload["path"])
    assert captured == Path(manifest.artifacts_dir) / "captures" / outside.name
    assert stat.S_IMODE(captured.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(captured.stat().st_mode) == 0o600
    assert payload["classification"] == "opaque-restricted"
    assert payload["upload_allowed"] is False
    assert payload["retention"] == "session"


def test_team_record_is_preflighted_confined_and_replayable_by_secret_ref(
    mock, capsys, tmp_path, monkeypatch
):
    manifest, _ = team_manifest(mock, tmp_path, authority="interaction")
    requested = tmp_path / "checkout.ndjson"
    code, _, err = run_team(
        mock,
        capsys,
        manifest,
        "record",
        "--output",
        str(requested),
        "--",
        "type",
        "#password",
        "literal-secret",
    )
    assert code == 1 and "exige @env" in err
    assert mock.commands == []

    secret = "team-record-canary-4728"
    monkeypatch.setenv("RECORDED_PASSWORD", secret)
    mock.on_eval("window.location.href", "http://demo.test/page")
    mock.on_eval(
        "__cdpx_actionability",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": True,
                "rect": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        ),
    )
    mock.on_eval("__cdpx_prepare_text", True)
    code, out, err = run_team(
        mock,
        capsys,
        manifest,
        "record",
        "--output",
        str(requested),
        "--",
        "type",
        "#password",
        "@env:RECORDED_PASSWORD",
    )
    assert code == 0 and not err and secret not in out
    payload = json.loads(out)
    journal_path = Path(payload["path"])
    assert journal_path == Path(manifest.artifacts_dir) / "journals" / requested.name
    assert not requested.exists()
    assert secret not in journal_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(journal_path.stat().st_mode) == 0o600

    code, out, err = run_team(mock, capsys, manifest, "replay", str(requested))
    assert code == 0 and not err and secret not in out
    assert json.loads(out)["played"] == 1
