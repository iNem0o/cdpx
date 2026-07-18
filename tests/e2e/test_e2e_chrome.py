"""Real Chrome E2E.

Chrome/Chromium is a mandatory dependency of the e2e gate: if no binary is
available, the suite fails instead of producing a false success via skip.
The scenarios run the same fixtures as the mock tests, but against a real
browser + the fixture server.

Launch target:
  chromium --headless=new --remote-debugging-port=0 ... (handled here)
  make test-e2e
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from cdpx import discovery, proof
from cdpx.action_model import ClickAction, GotoAction, TypeAction
from cdpx.client import CDPClient
from cdpx.orchestration import OrchestrationContext
from cdpx.primitives import (
    actions,
    audit,
    capture,
    dev,
    diagnostics,
    emulation,
    frames,
    inputs,
    interception,
    js,
    nav,
    net,
    recording,
    state,
)
from cdpx.session import SessionManifest, start_session, stop_session
from cdpx.testing.e2e import (
    attach_cli_run,
    attach_screenshot,
    free_loopback_port,
    run_cli,
    stop_process,
    successful_json,
    wait_for_chrome,
)
from cdpx.testing.evidence import ARTIFACT_TYPES

SCENARIO_FIXTURES = Path(__file__).parents[1] / "fixtures" / "scenarios"

CHROME_BIN = next(
    (
        b
        for b in (
            "chromium",
            "chromium-browser",
            "google-chrome",
            "google-chrome-stable",
            "chrome",
        )
        if shutil.which(b)
    ),
    None,
)

if CHROME_BIN is None:
    pytest.fail("Chrome/Chromium required for cdpx e2e", pytrace=False)


@pytest.fixture(scope="module")
def chrome():
    profile = tempfile.mkdtemp(prefix="cdpx-e2e-")
    port = free_loopback_port()
    log_path = Path(profile) / "chrome-stderr.log"
    stderr = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            CHROME_BIN,
            "--headless=new",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-sandbox",
            "--disable-gpu",
        ],
        stdout=subprocess.DEVNULL,
        stderr=stderr,
    )
    try:
        wait_for_chrome(proc, port, log_path)
        yield port
    finally:
        stop_process(proc)
        stderr.close()
        shutil.rmtree(profile, ignore_errors=True)


@pytest.fixture()
def page(chrome, fixtures_http, evidence_case):
    target = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    with CDPClient(target["webSocketDebuggerUrl"], timeout=15) as c:
        yield c, fixtures_http.base_url
        attach_screenshot(evidence_case, c, "final")
    discovery.close_tab("127.0.0.1", chrome, target["id"])


@pytest.fixture()
def cli_page(managed_cli_session, fixtures_http):
    manifest, path = managed_cli_session
    yield manifest, path, fixtures_http.base_url


@pytest.fixture(scope="module")
def managed_cli_session(tmp_path_factory):
    runtime = tmp_path_factory.mktemp("cdpx-managed-e2e")
    manifest, path = start_session(
        run_id="e2e-cli",
        authority="privileged",
        origins="http://127.0.0.1:*",
        ttl=900,
        owner_pid=os.getpid(),
        chrome_bin=CHROME_BIN,
        root=runtime,
    )
    try:
        yield manifest, path
    finally:
        if path.exists():
            stop_session(path, run_id=manifest.run_id, target_id=manifest.target_id)


def cli_json(session: tuple[SessionManifest, Path], *args: str) -> dict | list:
    manifest, path = session
    return successful_json(run_cli(manifest, path, *args))


def attach_cli_screenshot(
    evidence_case,
    session: tuple[SessionManifest, Path],
    label: str = "final",
) -> None:
    manifest, _ = session
    with CDPClient(manifest.websocket_url, timeout=10) as client:
        attach_screenshot(evidence_case, client, label)


@pytest.mark.scenario(
    feature="browser-navigation",
    journey="open-page",
    scenario_id="browser-navigation.open-page-success",
    proves=["The installed CLI manages a real Chrome target lifecycle."],
)
def test_cli_browser_lifecycle_black_box(managed_cli_session, fixtures_http, evidence_case):
    """The installed CLI drives a real Chrome lifecycle end to end: browser
    identity, navigation, then tab inventory on the supervised target —
    without ever touching a raw endpoint."""
    manifest, path = managed_cli_session
    version_proc = run_cli(manifest, path, "version")
    attach_cli_run(evidence_case, "cdpx version", version_proc)
    version = successful_json(version_proc)
    #: the browser answers with a real Chrome/Chromium identity
    assert version["Browser"].startswith(("Chrome/", "HeadlessChrome/", "Chromium/"))
    assert version["Protocol-Version"]

    navigated = cli_json(
        managed_cli_session,
        "goto",
        f"{fixtures_http.base_url}/index.html",
    )
    #: the navigation lands on the loopback reference site
    assert navigated["ok"] is True
    listed = cli_json(managed_cli_session, "tabs", "list")
    #: the supervised session only sees its own target
    assert listed["count"] == 1
    assert listed["tabs"][0]["id"] == manifest.target_id
    attach_cli_screenshot(evidence_case, managed_cli_session, "assigned-target")


@pytest.mark.scenario(
    feature="dom-interaction",
    journey="submit-form",
    scenario_id="dom-interaction.submit-form-like-user",
    proves=["The installed CLI submits a real form through trusted keyboard input."],
)
def test_cli_dom_and_keyboard_black_box(cli_page, evidence_case, monkeypatch):
    """A real form is submitted by trusted keyboard input, the secret value
    passing through the environment without ever appearing in argv."""
    manifest, path, base = cli_page
    session = (manifest, path)
    navigated = cli_json(session, "goto", f"{base}/form.html")
    #: the form page is loaded up to the load event
    assert navigated["ok"] is True and navigated["waited"] == "load"
    assert cli_json(session, "count", "input,button")["count"] == 3

    monkeypatch.setenv("E2E_FORM_TEXT", "Keyboard E2E")
    cli_json(session, "type", "#name", "--secret-env", "E2E_FORM_TEXT", "--clear")
    #: the Enter key triggers submission the way a human would
    assert cli_json(session, "key", "Enter")["pressed"] == "Enter"
    html = cli_json(session, "html", "#result")
    #: the final DOM proves the submission with the value typed on keyboard
    assert 'data-state="submitted"' in html["html"]
    assert "OK:Keyboard E2E" in html["html"]
    attach_cli_screenshot(evidence_case, session)


@pytest.mark.scenario(
    feature="browser-capture-observability",
    journey="capture-page",
    scenario_id="browser-capture-observability.persist-screenshot-proof",
    proves=["The installed CLI writes valid JPEG and PDF artifacts."],
)
def test_cli_jpeg_and_pdf_artifacts_black_box(cli_page, tmp_path, evidence_case):
    """The CLI's full-page JPEG and PDF captures produce real files of the
    announced format, confined to the supervised session's artifact
    directory rather than the raw requested path."""
    manifest, path, base = cli_page
    session = (manifest, path)
    cli_json(session, "goto", f"{base}/long.html")
    jpeg = tmp_path / "black-box-full.jpg"
    pdf = tmp_path / "black-box.pdf"

    shot = cli_json(
        session,
        "screenshot",
        "-o",
        str(jpeg),
        "--format",
        "jpeg",
        "--full-page",
    )
    printed = cli_json(session, "pdf", "-o", str(pdf))
    jpeg_path = Path(shot["path"])
    pdf_path = Path(printed["path"])
    #: the announced size matches the real signed JPEG file, and the raw
    #: path passed via -o is never written: the artifact is relocated under
    #: the session
    assert shot["format"] == "jpeg" and shot["full_page"] is True
    assert shot["bytes"] == jpeg_path.stat().st_size > 1000 and not jpeg.exists()
    assert jpeg_path.read_bytes().startswith(b"\xff\xd8\xff")
    #: same contract for the PDF: non-trivial file, %PDF- signature, session confinement
    assert printed["bytes"] == pdf_path.stat().st_size > 1000 and not pdf.exists()
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    if evidence_case is not None:
        # Binaries: JPEG relocated as type screenshot, PDF as type file — both
        # opaque-restricted (not inlined). The derived JSON makes size, format
        # and observed signatures readable without exposing binary content.
        evidence_case.attach_file(jpeg_path, "jpeg-full-page", "screenshot")
        evidence_case.attach_file(pdf_path, "pdf-print")
        evidence_case.attach_json(
            "observed-binary-artifacts",
            {
                "jpeg": {
                    "format": shot["format"],
                    "full_page": shot["full_page"],
                    "bytes": jpeg_path.stat().st_size,
                    "signature": jpeg_path.read_bytes()[:3].hex(),
                },
                "pdf": {
                    "bytes": pdf_path.stat().st_size,
                    "signature": pdf_path.read_bytes()[:5].decode("ascii"),
                },
            },
        )
    attach_cli_screenshot(evidence_case, session)


@pytest.mark.scenario(
    feature="browser-capture-observability",
    journey="inspect-runtime",
    scenario_id="browser-capture-observability.inspect-runtime-failures",
    proves=["Console follow streams bounded NDJSON from real Chrome."],
)
def test_cli_console_follow_is_bounded_ndjson(cli_page, evidence_case):
    """`console --follow --max N` stops on its own after exactly N NDJSON
    events from a real Chrome, mixing console logs and uncaught
    exceptions."""
    manifest, path, base = cli_page
    session = (manifest, path)
    cli_json(session, "goto", f"{base}/console.html")
    proc = run_cli(manifest, path, "console", "--follow", "--max", "4")
    attach_cli_run(evidence_case, "console-follow-max-4", proc)
    #: the bounded follow ends cleanly, with no stray diagnostic
    assert proc.returncode == 0 and proc.stderr == ""
    entries = [json.loads(line) for line in proc.stdout.splitlines()]
    #: each line of the stream is a self-contained JSON object and the --max bound is exact
    assert len(entries) == 4
    #: the stream mixes both event families, with the messages planted by the fixture
    assert {entry["kind"] for entry in entries} == {"console", "exception"}
    assert any("fixture-log" in entry["text"] for entry in entries)
    assert any("fixture-uncaught" in entry["text"] for entry in entries)
    attach_cli_screenshot(evidence_case, session)


@pytest.mark.scenario(
    feature="state-session",
    journey="prepare-session",
    scenario_id="state-session.prepare-repeatable-session",
    proves=["Cookie secrets stay masked and session storage is observable."],
)
def test_cli_cookie_masking_and_session_storage_black_box(cli_page, evidence_case, monkeypatch):
    """Masking sensitive values is the default end to end: cookies and
    sessionStorage come out masked, and a cookie set via the environment
    stays masked even under --show-values."""
    manifest, path, base = cli_page
    session_context = (manifest, path)
    cli_json(session_context, "goto", f"{base}/storage.html")
    secret = "synthetic-e2e-cookie-value"
    monkeypatch.setenv("E2E_COOKIE_VALUE", secret)
    set_result = cli_json(
        session_context,
        "cookies",
        "set",
        "--name",
        "blackBoxCookie",
        "--value-env",
        "E2E_COOKIE_VALUE",
        "--url",
        f"{base}/",
    )
    #: Chrome accepts the cookie whose secret value never transited through argv
    assert set_result["success"] is True

    masked_proc = run_cli(manifest, path, "cookies", "get")
    masked = successful_json(masked_proc)
    #: by default no value comes out: masking is announced, secret absent from the raw stream
    assert masked["values_masked"] is True
    assert secret not in masked_proc.stdout
    assert all(cookie["value"] == "***" for cookie in masked["cookies"])

    shown_proc = run_cli(manifest, path, "cookies", "get", "--show-values")
    shown = successful_json(shown_proc)
    #: even explicit unmasking does not reveal a secret coming from the environment
    assert shown["values_masked"] is False
    assert any(
        cookie["name"] == "blackBoxCookie" and cookie["value"] == "***"
        for cookie in shown["cookies"]
    )
    assert secret not in shown_proc.stdout
    if evidence_case is not None:
        # The masked run is safe (all values "***"): full transcript attached.
        # The --show-values run, on the other hand, reveals cookie values in
        # the clear (fixture values, not environment secrets) — so we do NOT
        # attach its raw transcript. The derived JSON proves the contrast
        # between masked-by-default and --show-values without exposing any value.
        attach_cli_run(evidence_case, "cookies-get-masked", masked_proc)
        evidence_case.attach_json(
            "cookies-masking-contrast",
            {
                "masked_run": {
                    "values_masked": masked["values_masked"],
                    "distinct_values": sorted({cookie["value"] for cookie in masked["cookies"]}),
                },
                "shown_run": {
                    "values_masked": shown["values_masked"],
                    "env_cookie_stays_masked": any(
                        cookie["name"] == "blackBoxCookie" and cookie["value"] == "***"
                        for cookie in shown["cookies"]
                    ),
                    "secret_absent_from_stdout": secret not in shown_proc.stdout,
                },
            },
        )
    session_storage = cli_json(session_context, "storage", "--kind", "session")
    #: sessionStorage applies the same default masking policy
    assert session_storage["entries"] == {"cdpx-session": "***"}
    assert session_storage["values_masked"] is True
    shown_session = cli_json(
        session_context,
        "storage",
        "--kind",
        "session",
        "--show-values",
    )
    #: --show-values returns the innocuous value actually set by the page
    assert shown_session["entries"] == {"cdpx-session": "oui"}
    #: cleanup brings the cookie context back to a verifiably clean state
    assert cli_json(session_context, "cookies", "clear")["cleared"] is True
    assert cli_json(session_context, "cookies", "get")["count"] == 0
    attach_cli_screenshot(evidence_case, session_context)


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="replay-flow",
    scenario_id="orchestration-control.orchestrate-replay-and-emulation",
    proves=["Network and CPU presets wrap a real composed navigation."],
)
def test_cli_slow_3g_and_cpu_emulation_black_box(cli_page, evidence_case):
    """The composed form `emulate <preset> -- goto` genuinely applies the
    network and CPU presets around a navigation, and faithfully reports the
    result of the delegated action."""
    manifest, path, base = cli_page
    session = (manifest, path)
    slow = cli_json(session, "emulate", "slow-3g", "--", "goto", f"{base}/index.html")
    #: the slow-3g preset is applied and the measured latency shows a real network slowdown
    assert slow["applied"] is True and slow["action"]["result"]["ok"] is True
    assert slow["action"]["result"]["elapsed_ms"] >= 200

    cpu = cli_json(session, "emulate", "cpu-4x", "--", "goto", f"{base}/index.html")
    #: the CPU preset also applies and the report identifies the composed action executed
    assert cpu["applied"] is True and cpu["action"]["result"]["ok"] is True
    assert cpu["action"]["argv"][0] == "goto"
    attach_cli_screenshot(evidence_case, session)


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["The CLI enforces stdout, stderr, and exit-code contracts end to end."],
)
def test_cli_stdout_stderr_and_exit_contract(cli_page, evidence_case):
    """The CLI contract (stdout = a single JSON object, stderr =
    diagnostics, exit 0/1/2) holds against a real Chrome for success,
    runtime error, and usage error."""
    manifest, path, base = cli_page
    session = (manifest, path)
    success = run_cli(manifest, path, "goto", f"{base}/form.html")
    attach_cli_run(evidence_case, "exit-0-success", success)
    #: success only uses stdout, with a single parsable JSON object
    assert success.returncode == 0 and success.stderr == ""
    assert json.loads(success.stdout)["ok"] is True

    runtime_error = run_cli(manifest, path, "click", "#missing")
    attach_cli_run(evidence_case, "exit-1-runtime-error", runtime_error)
    #: a missing selector is a runtime error: code 1, diagnostic outside stdout
    assert runtime_error.returncode == 1 and runtime_error.stdout == ""
    assert "selector not found" in runtime_error.stderr

    usage_error = run_cli(manifest, path, "goto")
    attach_cli_run(evidence_case, "exit-2-usage-error", usage_error)
    #: a malformed invocation stands out with code 2, reserved for usage errors
    assert usage_error.returncode == 2 and usage_error.stdout == ""
    assert "the following arguments are required: url" in usage_error.stderr
    attach_cli_screenshot(evidence_case, session)


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.publish-feature-proof",
    proves=["The offline Docs route renders the real session Mermaid diagrams."],
)
def test_proof_cockpit_renders_offline_docs_and_mermaid(page, tmp_path, evidence_case):
    """The shareable proof renders its Docs route entirely offline in a real
    Chrome: the session lifecycle Mermaid diagrams become SVGs with no
    external script and no network request."""
    client, _base = page
    summary = {
        "ok": True,
        "generated_at": "2026-07-15T00:00:00+00:00",
        "git": {"branch": "e2e", "sha": "test"},
        "feature_inventory": {"features": [], "totals": {}},
        "documentation": proof.build_documentation_catalog(),
        "totals": {},
        "scenario_totals": {},
    }
    proof_dir = tmp_path / ".proof"
    report = proof_dir / "proof-report.html"
    proof._write_private_text(report, proof.render_html(summary))
    staging = proof.build_shareable_proof(
        proof_dir,
        canaries=["never-present"],
        pre_redacted_paths={"proof-report.html"},
    )

    target = (
        staging / ".proof" / "proof-report.html"
    ).as_uri() + "#/docs/view/docs/SESSION-LIFECYCLE.md"
    #: the shareable version opens over file:// with no server at all
    assert nav.navigate(client, target)["ok"] is True
    nav.wait_for(client, ".panel.doc", timeout=20)
    runtime_state = js.evaluate(
        client,
        "({runtime: typeof window.mermaid, "
        "sources: document.querySelectorAll('pre.mermaid').length, title: document.title, "
        "app: document.querySelector('#app')?.textContent.slice(0, 80)})",
    )
    #: the Mermaid runtime is embedded in the page itself, not loaded from a CDN
    assert runtime_state["runtime"] == "object", runtime_state
    nav.wait_for(client, ".panel.doc .mermaid svg, .mermaid-error", timeout=20)
    mermaid_state = js.evaluate(
        client,
        "({"
        "svg: document.querySelectorAll('.panel.doc .mermaid svg').length,"
        "sources: document.querySelectorAll('.panel.doc pre.mermaid').length,"
        "errors: Array.from(document.querySelectorAll('.mermaid-error'), node => node.textContent),"
        "runtime: typeof window.mermaid"
        "})",
    )
    #: all four source diagrams are rendered to SVG with no parsing error
    assert mermaid_state == {"svg": 4, "sources": 4, "errors": [], "runtime": "object"}
    #: hermeticity proven: no external script declared, no network resource loaded
    external_scripts = js.evaluate(client, "document.querySelectorAll('script[src]').length")
    network_resources = js.evaluate(client, "performance.getEntriesByType('resource').length")
    assert external_scripts == 0
    assert network_resources == 0
    if evidence_case is not None:
        # Documented offline rendering: the Mermaid state (4 sources -> 4 SVG,
        # 0 error) and the hermeticity probes (no script[src], no network
        # resource) prove that the shareable Docs route holds on file:// alone.
        evidence_case.attach_json(
            "mermaid-offline-rendering",
            {
                "mermaid": mermaid_state,
                "hermeticity": {
                    "external_scripts": external_scripts,
                    "network_resources": network_resources,
                },
            },
        )

    js.evaluate(
        client,
        "location.hash = '#/docs/view/docs/features/state-session.md'",
    )
    nav.wait_for(client, ".panel.doc #intent")
    #: hash navigation to a feature sheet works and the offline menu references it
    assert "Session state and controls" in js.evaluate(
        client,
        "document.querySelector('#docsNav').innerText",
    )


# === Proof cockpit: behavioral coverage of the SPA ===
# A shareable report is generated ONCE per module (rendering ~4 MB) from a
# rich synthetic summary, then each test probes a view or a modal viewer via
# CDP. Everything goes through the cdpx.proof facade (render_html,
# build_shareable_proof): these tests are the behavioral safety net for the
# upcoming internal refactor of proof.py.

COCKPIT_FEATURE = "demo-checkout"
COCKPIT_JOURNEY = "buy-item"
COCKPIT_PASS_NODEID = "tests/e2e/demo_checkout.py::test_pay_success"
COCKPIT_FAIL_NODEID = "tests/e2e/demo_checkout.py::test_pay_declined"
COCKPIT_EVIDENCE_PREFIX = ".proof/evidence/artifacts/e2e/demo-checkout"


def _demo_cast() -> str:
    lines = [
        json.dumps({"version": 2, "width": 40, "height": 10}),
        json.dumps([0.2, "o", "$ cdpx tabs list\r\n"]),
        json.dumps([0.6, "o", '{"count": 1}\r\n']),
    ]
    return "\n".join(lines) + "\n"


def _artifact_bodies(screenshot_path: Path) -> dict[str, dict]:
    """One demo body per type of the closed taxonomy.

    Inlinable types (_INLINE_TYPES from proof.py) carry inline_content —
    an attach that fell back to the opaque `file` type would be invisible in
    the modal; screenshot points to a real PNG, video/file assume the
    download fallback.
    """

    console_payload = json.dumps(
        {
            "entries": [
                {"kind": "console", "type": "log", "text": "fixture-log ready"},
                {"kind": "console", "type": "warning", "text": "deprecated API"},
                {"kind": "exception", "type": "error", "text": "fixture-uncaught boom"},
            ]
        }
    )
    network_payload = json.dumps(
        {
            "summary": {"total": 2, "errors_4xx_5xx": 1, "failed": 0, "bytes": 640},
            "requests": [
                {
                    "method": "GET",
                    "url": "http://demo.test/api/json",
                    "status": 200,
                    "resourceType": "xhr",
                    "encodedBytes": 512,
                },
                {
                    "method": "GET",
                    "url": "http://demo.test/api/status/500",
                    "status": 500,
                    "resourceType": "xhr",
                    "encodedBytes": 128,
                },
            ],
        }
    )
    return {
        "asciinema": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/session.cast",
            "inline_content": _demo_cast(),
        },
        "command": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/goto.txt",
            "inline_content": (
                '$ cdpx goto http://demo.test/\n--- stdout ---\n{"ok": true}\n'
                "--- stderr ---\n\n--- exit_code: 0 ---\n"
            ),
            "meta": {
                "argv": ["cdpx", "goto", "http://demo.test/"],
                "exit_code": 0,
                "duration_s": 0.42,
            },
        },
        "console": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/console.json",
            "inline_content": console_payload,
        },
        "file": {"path": f"{COCKPIT_EVIDENCE_PREFIX}/dump.bin", "inline_skipped": "unreadable"},
        "json": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/verdict.json",
            "inline_content": json.dumps({"verdict": "pass", "steps": [1, 2, 3]}),
        },
        "log-excerpt": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/excerpt.txt",
            "inline_content": "healthy line\nERROR payment declined\nnext line",
            "meta": {"source": ".proof/app.log", "pattern": "ERROR", "matched_lines": [2]},
        },
        "logs": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/run.log",
            "inline_content": "line one\nline two\nline three",
        },
        "network": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/network.json",
            "inline_content": network_payload,
        },
        "profiler": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/profiler.json",
            "inline_content": json.dumps(
                {"profiler_status": 200, "token_present": True, "panels": {"db": {"queries": 6}}}
            ),
        },
        "screenshot": {"path": str(screenshot_path), "bytes": screenshot_path.stat().st_size},
        "video": {"path": f"{COCKPIT_EVIDENCE_PREFIX}/replay.webm", "inline_skipped": "size"},
    }


def _taxonomy_artifacts(screenshot_path: Path) -> list[dict]:
    bodies = _artifact_bodies(screenshot_path)
    artifacts = []
    # Iteration over the real taxonomy: a type added to ARTIFACT_TYPES with no
    # demo body here raises KeyError — the new type must receive its
    # synthetic proof AND its viewer, never a silent omission.
    for index, artifact_type in enumerate(sorted(ARTIFACT_TYPES)):
        body = bodies[artifact_type]
        artifacts.append(
            {
                "type": artifact_type,
                "label": f"demo-{artifact_type}",
                "bytes": len(body.get("inline_content", "")) or 512,
                "created_at": f"2026-07-15T00:00:{index + 1:02d}+00:00",
                **body,
            }
        )
    return artifacts


def _cockpit_run(
    nodeid: str,
    short_id: str,
    status: str,
    artifacts: list[dict],
    *,
    intent: str,
    assertions: list[dict],
    message: str = "",
    failed_line: int = 0,
) -> dict:
    return {
        "nodeid": nodeid,
        "suite": "e2e",
        "status": status,
        "feature": COCKPIT_FEATURE,
        "journey": COCKPIT_JOURNEY,
        "scenario": short_id,
        "scenario_id": f"{COCKPIT_FEATURE}.{short_id}",
        "started_at": "2026-07-15T00:00:00+00:00",
        "duration_s": 1.2,
        "intent": intent,
        "assertions": assertions,
        "failed_line": failed_line,
        "message": message,
        "artifacts": artifacts,
    }


def _proofs_for(run: dict) -> list[dict]:
    return [
        {
            "scenario": run["nodeid"],
            "scenario_id": run["scenario_id"],
            "type": artifact["type"],
            "label": artifact["label"],
            "path": artifact["path"],
        }
        for artifact in run["artifacts"]
    ]


def _scenario_node(short_id: str, title: str, texts: dict[str, str], run: dict) -> dict:
    return {
        "id": short_id,
        "scenario_id": f"{COCKPIT_FEATURE}.{short_id}",
        "journey": COCKPIT_JOURNEY,
        "title": title,
        "tests": [run["nodeid"]],
        "expected_proofs": ["junit"],
        "matched_tests": [run["nodeid"]],
        "matched_scenarios": [run],
        "proofs": _proofs_for(run),
        "gaps": [],
        **texts,
    }


def _cockpit_summary(screenshot_path: Path) -> dict:
    help_proc = subprocess.run(
        [sys.executable, "-m", "cdpx.cli", "--help"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    help_commands = proof.parse_help_commands(help_proc.stdout)
    run_pass = _cockpit_run(
        COCKPIT_PASS_NODEID,
        "pay-success",
        "passed",
        _taxonomy_artifacts(screenshot_path),
        intent="The customer settles their cart and receives proof of purchase.",
        assertions=[
            {
                "line": 12,
                "end_line": 12,
                "text": "the cart is charged the correct amount",
                "code_excerpt": "assert total == 42",
                "kind": "assert",
                "status": "",
            },
            {
                "line": 15,
                "end_line": 15,
                "text": "the receipt is issued to the customer",
                "code_excerpt": "assert receipt.sent",
                "kind": "assert",
                "status": "",
            },
        ],
    )
    run_fail = _cockpit_run(
        COCKPIT_FAIL_NODEID,
        "pay-declined",
        "failed",
        [
            {
                "type": "screenshot",
                "label": "payment-failure",
                "path": str(screenshot_path),
                "bytes": screenshot_path.stat().st_size,
                "created_at": "2026-07-15T00:00:02+00:00",
            }
        ],
        intent="A declined payment must stay a visible failure, never a false green.",
        assertions=[
            {
                "line": 30,
                "end_line": 30,
                "text": "the order is created",
                "code_excerpt": "assert order.id",
                "kind": "assert",
                "status": "",
            },
            {
                "line": 34,
                "end_line": 34,
                "text": "the payment is accepted",
                "code_excerpt": "assert paid",
                "kind": "assert",
                "status": "failed",
            },
        ],
        message="AssertionError: payment declined",
        failed_line=34,
    )
    node_pass = _scenario_node(
        "pay-success",
        "Pay successfully",
        {
            "ui_text": "The customer settles their cart.",
            "report_text": "This scenario proves the nominal payment.",
            "given": "A cart ready to be settled.",
            "when": "The customer confirms the payment.",
            "then": "The receipt is issued.",
        },
        run_pass,
    )
    node_fail = _scenario_node(
        "pay-declined",
        "Payment declined",
        {
            "ui_text": "A declined payment is reported to the customer.",
            "report_text": "This scenario proves the visibility of payment declines.",
            "given": "A card declined by the bank.",
            "when": "The customer attempts to pay.",
            "then": "The decline is explained with no receipt issued.",
        },
        run_fail,
    )
    journey = {
        "id": COCKPIT_JOURNEY,
        "title": "Buy an item",
        "entrypoint": "cdpx goto",
        "scenarios": [node_pass, node_fail],
        "matched_tests": [COCKPIT_PASS_NODEID, COCKPIT_FAIL_NODEID],
        "matched_scenarios": [run_pass, run_fail],
        "proofs": _proofs_for(run_pass) + _proofs_for(run_fail),
        "gaps": [],
    }
    feature = {
        "id": COCKPIT_FEATURE,
        "title": "Demo checkout",
        "status": "active",
        "summary": "Synthetic checkout flow built to exercise the cockpit.",
        "entrypoints": ["cdpx goto"],
        "path_globs": [],
        "test_globs": [],
        "docs": [],
        "journeys": [journey],
        "scenarios": [node_pass, node_fail],
        "expected_proofs": ["junit"],
        "source": "docs/features/demo-checkout.md",
        "sections": [],
        "doc_html": ("<h2>Usage</h2><p>Demo documentation for the checkout flow.</p>"),
        "matched_entrypoints": [],
        "matched_paths": [],
        "matched_tests": [COCKPIT_PASS_NODEID, COCKPIT_FAIL_NODEID],
        "matched_scenarios": [run_pass, run_fail],
        "proofs": _proofs_for(run_pass) + _proofs_for(run_fail),
        "changed_paths": [],
        "gaps": [],
    }
    entrypoints = [
        {
            "id": f"cdpx {command['name']}",
            "type": "cli",
            "source": "src/cdpx/cli.py",
            "label": command.get("help", ""),
        }
        for command in help_commands
    ]
    cast_text = _demo_cast()
    return {
        "ok": False,
        "generated_at": "2026-07-15T00:00:00+00:00",
        "git": {"branch": "e2e-cockpit", "sha": "0000000", "changed_files": []},
        "environment": {"python": "3.12", "platform": "e2e-fixture", "chrome_or_chromium": True},
        "cli_help": ".proof/cdpx-help.txt",
        "totals": {"tests": 13, "passed": 12, "skipped": 0, "failed": 1, "unavailable": 0},
        "scenario_totals": {
            "scenarios": 2,
            "unit": 0,
            "integration": 0,
            "e2e": 2,
            "symfony": 0,
            "screenshots": 2,
            "missing_e2e_screenshots": [],
        },
        "scenario_evidence": {
            "suites": {"unit": [], "integration": [], "e2e": [run_pass, run_fail], "symfony": []},
            "files": [],
            "totals": {
                "scenarios": 2,
                "unit": 0,
                "integration": 0,
                "e2e": 2,
                "symfony": 0,
                "screenshots": 2,
                "missing_e2e_screenshots": [],
            },
        },
        "feature_inventory": {
            "features": [feature],
            "entrypoints": entrypoints,
            "feature_by_entrypoint": {"cdpx goto": COCKPIT_FEATURE},
            "totals": {
                "features": 1,
                "entrypoints": len(entrypoints),
                "mapped_entrypoints": 1,
                "scenarios": 2,
                "documented_scenarios": 2,
                "warnings": 1,
                "violations": 1,
            },
            "violations": ["scenario unmapped: tests/e2e/demo_checkout.py::test_flaky"],
            "warnings": ["source path unmapped: src/demo/extra.py"],
            "docs_dir": "docs/features",
        },
        "documentation": {"documents": [], "tree": {}},
        "commands": [
            {
                "id": "ruff-check",
                "label": "Ruff lint",
                "argv": ["ruff", "check", "src", "tests"],
                "log": ".proof/ruff-check.log",
                "exit_code": 0,
                "duration_s": 2.5,
                "status": "ok",
                "log_tail": "All checks passed!",
            },
            {
                "id": "unit",
                "label": "Unit pytest",
                "argv": ["pytest", "tests"],
                "log": ".proof/make-check-pytest.log",
                "exit_code": 0,
                "duration_s": 30.0,
                "status": "ok",
                "log_tail": "430 passed",
            },
            {
                "id": "e2e",
                "label": "Pytest E2E Chrome",
                "argv": ["pytest", "tests/e2e"],
                "log": ".proof/e2e-chrome.log",
                "exit_code": 1,
                "duration_s": 61.4,
                "status": "failed",
                "log_tail": f"FAILED {COCKPIT_FAIL_NODEID}",
            },
        ],
        "junit": {
            "unit": {
                "path": ".proof/unit-junit.xml",
                "exists": True,
                "tests": 10,
                "passed": 10,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
                "time_s": 3.2,
                "parse_error": None,
                "cases": [],
                "focus": [
                    {
                        "classname": "tests.test_cli",
                        "name": "test_pretty",
                        "time_s": 0.5,
                        "status": "passed",
                        "message": "",
                    }
                ],
            },
            "e2e": {
                "path": ".proof/e2e-junit.xml",
                "exists": True,
                "tests": 2,
                "passed": 1,
                "failures": 1,
                "errors": 0,
                "skipped": 0,
                "time_s": 61.0,
                "parse_error": None,
                "cases": [],
                "focus": [
                    {
                        "classname": "tests.e2e.demo_checkout",
                        "name": "test_pay_declined",
                        "time_s": 1.4,
                        "status": "failed",
                        "message": "AssertionError",
                    }
                ],
            },
            "symfony": {
                "path": ".proof/symfony-e2e-junit.xml",
                "exists": True,
                "tests": 1,
                "passed": 1,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
                "time_s": 12.0,
                "parse_error": None,
                "cases": [],
                "focus": [],
            },
        },
        "casts": [
            {
                "id": "cdpx-help",
                "status": "generated",
                "path": ".proof/cdpx-help.cast",
                "bytes": len(cast_text),
            }
        ],
        "evidence_catalog": [
            {
                "type": "asciinema",
                "name": "cdpx-help",
                "path": ".proof/cdpx-help.cast",
                "status": "generated",
                "roi": "Terminal replay of the demo.",
                "inline_content": cast_text,
            },
            {
                "type": "screenshot",
                "name": "Capture UI",
                "path": "",
                "status": "not-needed",
                "roi": "Not generated automatically.",
            },
        ],
        "project": {
            "name": "cdpx",
            "version": "0.0-e2e",
            "mission": ("Chrome DevTools Protocol primitives CLI for the demo proof."),
            "cli_command_count": len(help_commands),
            "cli_commands": [command["name"] for command in help_commands],
            "docs": ["README.md", "HARNESS.md"],
            "fixtures": ["tests/fixtures/index.html"],
        },
        "validation_matrix": [{"milestone": "M9", "proof": "Secondary proofs generalized"}],
        "coverage_groups": [
            {"suite": "e2e", "module": "demo_checkout", "tests": 2, "failed": 1, "skipped": 0}
        ],
        "risks": [
            {
                "risk": "Chrome required.",
                "mitigation": "make proof fails without a binary.",
                "rollback": "Install Chrome then retry.",
            }
        ],
        "unknowns": [
            {
                "item": "External network",
                "why": "Loopback fixtures only.",
                "how_to_verify": "Inspect the network logs.",
            }
        ],
        "proof_failures": ["command failed: Pytest E2E Chrome (.proof/e2e-chrome.log)"],
    }


@pytest.fixture(scope="module")
def cockpit_report(tmp_path_factory):
    """Shareable proof report generated once for the whole module: the HTML
    rendering (~4 MB, Mermaid + xterm bundles included) is too costly to
    rebuild for every SPA test."""
    root = tmp_path_factory.mktemp("cdpx-cockpit")
    screenshot = root / "demo-capture.png"
    screenshot.write_bytes((Path(__file__).parents[1] / "fixtures" / "pixel.png").read_bytes())
    summary = _cockpit_summary(screenshot)
    proof_dir = root / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", proof.render_html(summary))
    staging = proof.build_shareable_proof(
        proof_dir,
        canaries=["never-present"],
        pre_redacted_paths={"proof-report.html"},
    )
    return (staging / ".proof" / "proof-report.html").as_uri()


def _js_ready(client: CDPClient, expression: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if js.evaluate(client, expression) is True:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"cockpit never ready: {expression}")
        time.sleep(0.05)


def _click(client: CDPClient, selector: str) -> None:
    clicked = js.evaluate(
        client,
        "(() => { const el = document.querySelector(" + json.dumps(selector) + ");"
        " if (!el) return false; el.click(); return true; })()",
    )
    assert clicked is True, f"element not found on click: {selector}"


def _open_cockpit(client: CDPClient, report_url: str, route: str, ready: str) -> None:
    assert nav.navigate(client, f"{report_url}#{route}")["ok"] is True
    _js_ready(client, ready)


def _goto_route(client: CDPClient, route: str, ready: str) -> None:
    js.evaluate(client, "location.hash = " + json.dumps("#" + route))
    _js_ready(client, ready)


def _open_artifact_modal(client: CDPClient, artifact_type: str) -> None:
    selector = (
        "#app .timeline-row .shot"
        if artifact_type == "screenshot"
        else f'#app .timeline-row .chip[title="{artifact_type}"]'
    )
    _click(client, selector)
    state = js.evaluate(
        client,
        "({hidden: document.getElementById('artifact-modal').hidden,"
        " type: document.querySelector('.modal-type').textContent})",
    )
    assert state == {"hidden": False, "type": artifact_type}, state


def _close_modal(client: CDPClient) -> None:
    inputs.press_key(client, "Escape")
    assert js.evaluate(client, "document.getElementById('artifact-modal').hidden") is True


def _expand_test_card(client: CDPClient) -> None:
    """Expand the test card the way a reader would.

    A passed test's card is a <details> collapsed by default: its content
    responds to programmatic click() but is not focusable while the card is
    closed — a real user must open it to reach the chips.
    """

    expanded = js.evaluate(
        client,
        "(() => { const card = document.querySelector('#app .test-card');"
        " if (!card) return false; card.open = true; return card.open; })()",
    )
    assert expanded is True


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.navigate-cockpit-views",
    proves=["The cockpit SPA drills down from features to journeys, scenarios and test cards."],
)
def test_cockpit_features_view_drills_down_to_scenario(page, cockpit_report, evidence_case):
    """The cockpit home renders verdict and metrics, then the Features ->
    sheet -> journey -> scenario drill-down lands on the test card with
    breadcrumb, status pills, BDD block and annotated timeline."""
    client, _base = page
    _open_cockpit(client, cockpit_report, "/features", "!!document.querySelector('#app .metrics')")

    home = js.evaluate(
        client,
        "({verdict: document.querySelector('.metrics .metric strong').textContent,"
        " tests: document.querySelectorAll('.metrics .metric strong')[1].textContent,"
        " cards: document.querySelectorAll('#app .grid .card').length,"
        " cardPill: document.querySelector('#app .grid .card .pill').textContent,"
        " sideEntry: document.querySelector("
        "'#featureNav a[data-feature-id=\"demo-checkout\"]').textContent})",
    )
    #: the home announces the red verdict and the run's test count, and the
    #: synthetic feature appears as a card as well as in the sidebar
    assert home["verdict"] == "FAILED"
    assert home["tests"] == "12/13"
    assert home["cards"] == 1 and home["cardPill"] == "failed"
    assert "Demo checkout" in home["sideEntry"]
    assert "1 journeys" in home["sideEntry"]

    _click(client, '#app .grid .card h2 a[href="#/features/demo-checkout"]')
    _js_ready(client, "document.querySelector('#app h1')?.textContent === 'Demo checkout'")
    feature_view = js.evaluate(
        client,
        "({crumbs: document.querySelector('#app .crumbs').textContent,"
        " pill: document.querySelector('#app .meta .pill').textContent,"
        " doc: document.querySelector('#app .panel.doc').textContent,"
        " journeyLink: !!document.querySelector("
        "'#app a[href=\"#/features/demo-checkout/journeys/buy-item\"]')})",
    )
    #: the feature sheet carries its breadcrumb, its failure pill, its
    #: rendered user doc and the link to its journey
    assert "Features" in feature_view["crumbs"]
    assert feature_view["pill"] == "failed"
    assert "Demo documentation" in feature_view["doc"]
    assert feature_view["journeyLink"] is True

    _click(client, '#app a[href="#/features/demo-checkout/journeys/buy-item"]')
    _js_ready(client, "document.querySelector('#app h1')?.textContent === 'Buy an item'")
    journey_view = js.evaluate(
        client,
        "({entrypoint: document.querySelector('#app p code').textContent,"
        " rows: document.querySelectorAll('#app .scenario-list .scenario-row').length,"
        " pills: Array.from("
        "document.querySelectorAll('#app .scenario-row .pill'), n => n.textContent)})",
    )
    #: the journey lists its two documented scenarios with their own verdict
    assert journey_view["entrypoint"] == "cdpx goto"
    assert journey_view["rows"] == 2
    assert journey_view["pills"] == ["ok", "failed"]

    _click(client, '#app a[href="#/features/demo-checkout/scenarios/pay-success"]')
    _js_ready(client, "document.querySelector('#app h1')?.textContent === 'Pay successfully'")
    scenario_view = js.evaluate(
        client,
        "({crumbLinks: document.querySelectorAll('#app .crumbs a').length,"
        " bdd: Array.from(document.querySelectorAll('#app .bdd h3'), n => n.textContent),"
        " given: document.querySelector('#app .bdd div p').textContent,"
        " cardPill: document.querySelector('#app .test-card summary .pill').textContent,"
        " nodeid: document.querySelector('#app .test-card summary code').textContent,"
        " intent: document.querySelector('#app .test-intent').textContent,"
        " okMarks: document.querySelectorAll('#app .assertion-row.assertion-ok').length,"
        " timeline: document.querySelectorAll('#app .timeline-row').length})",
    )
    #: the scenario sheet traces back to the test: full breadcrumb,
    #: documented Given/When/Then block, passed test card with intent
    assert scenario_view["crumbLinks"] == 3
    assert scenario_view["bdd"] == ["Given", "When", "Then"]
    assert scenario_view["given"] == "A cart ready to be settled."
    assert scenario_view["cardPill"] == "passed"
    assert scenario_view["nodeid"] == COCKPIT_PASS_NODEID
    assert "settles their cart" in scenario_view["intent"]
    #: the annotated timeline paints both assertions of the passed test in
    #: green and the timeline exposes one artifact per taxonomy type
    assert scenario_view["okMarks"] == 2
    assert scenario_view["timeline"] == len(ARTIFACT_TYPES)
    if evidence_case is not None:
        evidence_case.attach_json(
            "cockpit-drilldown",
            {
                "home": home,
                "feature": feature_view,
                "journey": journey_view,
                "scenario": scenario_view,
            },
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.navigate-cockpit-views",
    proves=["A red run surfaces read-first failures and a dedicated gaps view."],
)
def test_cockpit_read_first_and_gaps_surface_failures(page, cockpit_report, evidence_case):
    """On a red run, the home opens with "Read first" (proof failures and
    failed tests whose link leads to their scenario sheet), the top bar
    counts the gaps, and the #/gaps route details violations, warnings and
    proof failures."""
    client, _base = page
    _open_cockpit(
        client, cockpit_report, "/features", "!!document.querySelector('#app .read-first')"
    )

    read_first = js.evaluate(
        client,
        "({heading: document.querySelector('.read-first h2').textContent,"
        " items: Array.from("
        "document.querySelectorAll('.read-first li'), n => n.textContent.trim()),"
        " failedPill: document.querySelector('.read-first li .pill.failed')?.textContent,"
        " failedHref: document.querySelector('.read-first li a')?.getAttribute('href'),"
        " gapsSup: document.querySelector('[data-route=\"/gaps\"] sup')?.textContent,"
        " gapsSupBad: !!document.querySelector('[data-route=\"/gaps\"] sup.sup-bad'),"
        " runSup: document.querySelector('[data-route=\"/run\"] sup.sup-bad')?.textContent})",
    )
    #: the "Read first" panel names the failed command AND the failed test,
    #: with its status pill
    assert read_first["heading"] == "Read first"
    assert any("command failed: Pytest E2E Chrome" in item for item in read_first["items"])
    assert any(COCKPIT_FAIL_NODEID in item for item in read_first["items"])
    assert read_first["failedPill"] == "failed"
    #: the top bar aggregates the gaps (1 violation + 1 warning + 1 proof
    #: failure) and the number of failed tests on the Run link
    assert read_first["gapsSup"] == "3" and read_first["gapsSupBad"] is True
    assert read_first["runSup"] == "1"

    _click(client, ".read-first li a")
    _js_ready(client, "document.querySelector('#app h1')?.textContent === 'Payment declined'")
    failed_scenario = js.evaluate(
        client,
        "({title: document.querySelector('#app h1').textContent,"
        " crumbs: document.querySelector('#app .crumbs').textContent})",
    )
    #: the failed test's link lands on its scenario sheet (and not on the
    #: "View not found" fallback): declined scenario's title and full breadcrumb
    assert failed_scenario["title"] == "Payment declined"
    assert "Demo checkout" in failed_scenario["crumbs"]
    assert "Buy an item" in failed_scenario["crumbs"]

    _goto_route(
        client, "/gaps", "document.querySelector('#app h1')?.textContent === 'Gaps and violations'"
    )
    gaps = js.evaluate(
        client,
        "({panels: Array.from(document.querySelectorAll('#app .panel h2'), n => n.textContent),"
        " violations: document.querySelectorAll('#app .panel')[0].textContent,"
        " warnings: document.querySelectorAll('#app .panel')[1].textContent,"
        " failures: document.querySelectorAll('#app .panel')[2].textContent})",
    )
    #: the Gaps view separates inventory violations, warnings and proof
    #: failures, each rendering its exact textual diagnostic
    assert gaps["panels"] == ["Violations", "Warnings", "Proof failures"]
    assert "scenario unmapped" in gaps["violations"]
    assert "source path unmapped" in gaps["warnings"]
    assert "command failed: Pytest E2E Chrome" in gaps["failures"]
    if evidence_case is not None:
        evidence_case.attach_json(
            "read-first-and-gaps",
            {"read_first": read_first, "failed_scenario": failed_scenario, "gaps": gaps},
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.inspect-artifact-viewers",
    proves=["Every inlined textual artifact type opens in its dedicated modal viewer."],
)
def test_modal_renders_every_textual_viewer(page, cockpit_report, evidence_case):
    """Every inlined textual proof opens in its dedicated viewer: console
    filterable by level, network table, JSON tree, profiler, numbered logs,
    highlighted log excerpt and command transcript."""
    client, _base = page
    _open_cockpit(
        client,
        cockpit_report,
        "/features/demo-checkout/scenarios/pay-success",
        "!!document.querySelector('#app .artifact-timeline')",
    )
    _expand_test_card(client)

    chips = js.evaluate(
        client,
        "({total: document.querySelectorAll('#app .timeline-row a').length,"
        " openable: document.querySelectorAll('#app .timeline-row [data-modal-group]').length})",
    )
    #: ratchet: every type of the closed taxonomy produces a chip able to
    #: open the modal — a type added without a viewer would break this count
    assert chips == {"total": len(ARTIFACT_TYPES), "openable": len(ARTIFACT_TYPES)}

    _open_artifact_modal(client, "console")
    console_view = js.evaluate(
        client,
        "({filters: Array.from("
        "document.querySelectorAll('.modal-content .console-filter'), n => n.textContent.trim()),"
        " lines: document.querySelectorAll('.modal-content .console-line').length})",
    )
    #: the console counts its messages by level and renders each line
    assert console_view["filters"] == ["error (1)", "warn (1)", "log (1)"]
    assert console_view["lines"] == 3
    _click(client, '.modal-content [data-console-level="log"]')
    #: unchecking a level hides exactly the lines of that level
    assert (
        js.evaluate(
            client, "document.querySelectorAll('.modal-content .console-line[hidden]').length"
        )
        == 1
    )
    _close_modal(client)

    _open_artifact_modal(client, "network")
    network_view = js.evaluate(
        client,
        "({summary: document.querySelector('.modal-content .viewer-summary').textContent,"
        " rows: document.querySelectorAll('.modal-content tbody tr').length,"
        " bad: document.querySelectorAll('.modal-content .net-status.net-bad').length,"
        " ok: document.querySelectorAll('.modal-content .net-status.net-ok').length})",
    )
    #: the network table renders the aggregated summary and colors the HTTP statuses
    assert "2 requests" in network_view["summary"]
    assert "1 4xx/5xx errors" in network_view["summary"]
    assert network_view["rows"] == 2
    assert network_view["bad"] == 1 and network_view["ok"] == 1
    _close_modal(client)

    _open_artifact_modal(client, "json")
    json_view = js.evaluate(
        client,
        "({nodes: document.querySelectorAll("
        "'.modal-content .json-view details.json-node').length,"
        " keys: Array.from("
        "document.querySelectorAll('.modal-content .json-key'), n => n.textContent)})",
    )
    #: JSON is rendered as a collapsible tree, with visible keys
    assert json_view["nodes"] >= 2
    assert "verdict" in json_view["keys"] and "steps" in json_view["keys"]
    _close_modal(client)

    _open_artifact_modal(client, "profiler")
    profiler_view = js.evaluate(
        client,
        "({chips: document.querySelectorAll('.modal-content .viewer-summary .chip').length,"
        " tree: !!document.querySelector('.modal-content .json-view')})",
    )
    #: the profiler summarizes its scalars as chips and keeps the full JSON tree
    assert profiler_view["chips"] == 2 and profiler_view["tree"] is True
    _close_modal(client)

    _open_artifact_modal(client, "logs")
    logs_view = js.evaluate(
        client,
        "({lines: document.querySelectorAll('.modal-content .log-view .log-line').length,"
        " numbered: document.querySelectorAll('.modal-content .log-num').length})",
    )
    #: full logs are numbered line by line
    assert logs_view == {"lines": 3, "numbered": 3}
    _close_modal(client)

    _open_artifact_modal(client, "log-excerpt")
    excerpt_view = js.evaluate(
        client,
        "({banner: document.querySelector('.modal-content .viewer-summary').textContent,"
        " hits: document.querySelectorAll('.modal-content .log-hit').length,"
        " numbered: document.querySelectorAll('.modal-content .log-num').length})",
    )
    #: the excerpt shows source and pattern ("motif"), highlights the
    #: matching line and does not number (the original file's line numbers
    #: are lost)
    assert "source" in excerpt_view["banner"] and "pattern" in excerpt_view["banner"]
    assert excerpt_view["hits"] == 1 and excerpt_view["numbered"] == 0
    _close_modal(client)

    _open_artifact_modal(client, "command")
    command_view = js.evaluate(
        client,
        "({exit: document.querySelector('.modal-content .command-head .pill').textContent,"
        " argv: document.querySelector('.modal-content .command-head code').textContent,"
        " stdout: document.querySelector('.modal-content .stream-out pre').textContent,"
        " stderr: document.querySelector('.modal-content .stream-err pre').textContent})",
    )
    #: the command transcript separates stdout/stderr, recalls the exact
    #: argv and the exit code as a pill
    assert command_view["exit"] == "exit 0"
    assert command_view["argv"] == "$ cdpx goto http://demo.test/"
    assert '{"ok": true}' in command_view["stdout"]
    assert command_view["stderr"] == "(empty)"
    _close_modal(client)
    if evidence_case is not None:
        evidence_case.attach_json(
            "textual-viewers",
            {
                "chips": chips,
                "console": console_view,
                "network": network_view,
                "json": json_view,
                "profiler": profiler_view,
                "logs": logs_view,
                "log_excerpt": excerpt_view,
                "command": command_view,
            },
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.inspect-artifact-viewers",
    proves=["Media artifacts get zoom, download fallback and an embedded xterm cast player."],
)
def test_modal_renders_media_and_cast_viewers(page, cockpit_report, evidence_case):
    """Non-inlinable proofs have their own viewer: zoomable screenshot with
    relative timestamp, video in a local native player, opaque file with a
    download fallback, and an asciinema cast played in an xterm terminal."""
    client, _base = page
    _open_cockpit(
        client,
        cockpit_report,
        "/features/demo-checkout/scenarios/pay-success",
        "!!document.querySelector('#app .artifact-timeline')",
    )
    _expand_test_card(client)

    _open_artifact_modal(client, "screenshot")
    shot = js.evaluate(
        client,
        "({img: !!document.querySelector("
        "'.modal-content figure.viewer-media img[data-zoomable]'),"
        " captured: document.querySelector('.modal-context-body').textContent})",
    )
    #: the image is zoomable and the context dates the capture relative to
    #: the test run's start
    assert shot["img"] is True
    assert "(+" in shot["captured"]
    _click(client, ".modal-content img[data-zoomable]")
    #: a click zooms in, the next one restores — toggle with no residual state
    assert (
        js.evaluate(
            client, "document.querySelector('.modal-content img').classList.contains('zoomed')"
        )
        is True
    )
    _click(client, ".modal-content img[data-zoomable]")
    assert (
        js.evaluate(
            client, "document.querySelector('.modal-content img').classList.contains('zoomed')"
        )
        is False
    )
    _close_modal(client)

    _open_artifact_modal(client, "video")
    video_view = js.evaluate(
        client,
        "({player: !!document.querySelector('.modal-content video[controls]'),"
        " src: document.querySelector('.modal-content video')?.getAttribute('src')})",
    )
    #: the video (never inlined) is served by a native player pointing at
    #: the private proof's local file
    assert video_view["player"] is True
    assert video_view["src"] == "evidence/artifacts/e2e/demo-checkout/replay.webm"
    _close_modal(client)

    _open_artifact_modal(client, "file")
    fallback = js.evaluate(
        client,
        "({text: document.querySelector('.modal-content .viewer-fallback').textContent,"
        " link: document.querySelector('.modal-content .viewer-fallback a')?.textContent})",
    )
    #: an opaque file owns its fallback: reason for not embedding and a
    #: download link to the artifact
    assert "Content not embedded" in fallback["text"]
    assert fallback["link"] == "open the file"
    _close_modal(client)

    _open_artifact_modal(client, "asciinema")
    cast_view = js.evaluate(
        client,
        "({xterm: !!document.querySelector('.modal-content [data-cast-screen] .xterm'),"
        " time: document.querySelector('.modal-content [data-cast-time]').textContent,"
        " scrubMax: document.querySelector('.modal-content [data-cast-scrub]').max,"
        " play: document.querySelector('.modal-content [data-cast-play]').textContent})",
    )
    #: the cast player initializes a real xterm terminal, settled at the end
    #: of the cast, with a scrubber bounded to the real duration and a play button
    assert cast_view["xterm"] is True
    assert cast_view["time"] == "0.6s / 0.6s"
    assert cast_view["scrubMax"] == "600"
    assert "play" in cast_view["play"]
    _click(client, ".modal-content [data-cast-rawtoggle]")
    raw_view = js.evaluate(
        client,
        "({raw: document.querySelector('.modal-content [data-cast-raw]').hidden,"
        " screen: document.querySelector('.modal-content [data-cast-screen]').hidden,"
        " text: document.querySelector('.modal-content [data-cast-raw]').textContent})",
    )
    #: the raw fallback view replaces the screen and renders the cast's text
    #: stripped of control sequences
    assert raw_view["raw"] is False and raw_view["screen"] is True
    assert "cdpx tabs list" in raw_view["text"]
    _close_modal(client)
    if evidence_case is not None:
        evidence_case.attach_json(
            "media-and-cast-viewers",
            {"screenshot": shot, "video": video_view, "file": fallback, "cast": cast_view},
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.inspect-artifact-viewers",
    proves=["The artifact modal is fully keyboard drivable with a focus trap."],
)
def test_modal_keyboard_navigation_and_focus_trap(page, cockpit_report, evidence_case):
    """The modal is fully keyboard drivable: initial focus on Close,
    previous/next arrows bounded to the list, Tab trapped at the modal's
    extremities, and Escape closes while restoring focus to the origin
    element."""
    client, _base = page
    _open_cockpit(
        client,
        cockpit_report,
        "/features/demo-checkout/scenarios/pay-success",
        "!!document.querySelector('#app .artifact-timeline')",
    )
    #: the card must be expanded for the chip to be focusable, a condition
    #: for the focus restoration tested on close
    _expand_test_card(client)
    chip_selector = '#app .timeline-row .chip[title="command"]'
    opened = js.evaluate(
        client,
        "(() => { const el = document.querySelector(" + json.dumps(chip_selector) + ");"
        " if (!el) return false; el.focus(); el.click(); return true; })()",
    )
    assert opened is True
    #: on open, focus jumps to the modal's Close button
    assert js.evaluate(client, "document.activeElement.classList.contains('modal-close')") is True

    order = sorted(ARTIFACT_TYPES)
    total = len(order)
    position = order.index("command") + 1
    counter = "document.querySelector('.modal-counter').textContent"
    #: the counter locates the open artifact in the test's timeline
    assert js.evaluate(client, counter) == f"{position}/{total}"
    inputs.press_key(client, "ArrowLeft")
    assert js.evaluate(client, counter) == f"{position - 1}/{total}"
    inputs.press_key(client, "ArrowLeft")
    #: the previous arrow is bounded: no going past the list at the first item
    assert js.evaluate(client, counter) == f"{position - 1}/{total}"
    inputs.press_key(client, "ArrowRight")
    #: the next arrow returns exactly to the starting artifact
    assert js.evaluate(client, counter) == f"{position}/{total}"

    focusables_expr = (
        "Array.from(document.getElementById('artifact-modal')"
        ".querySelectorAll('button, a[href], video'))"
    )
    focus_count = js.evaluate(
        client,
        "(() => { const f = " + focusables_expr + "; f[f.length - 1].focus();"
        " return f.length; })()",
    )
    assert focus_count >= 2
    inputs.press_key(client, "Tab")
    #: from the last focusable, Tab loops back to the first (Close button)
    assert js.evaluate(client, "document.activeElement.classList.contains('modal-close')") is True
    shift_tab = {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9, "modifiers": 8}
    client.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", **shift_tab})
    client.send("Input.dispatchKeyEvent", {"type": "keyUp", **shift_tab})
    #: Shift+Tab from the first focusable wraps back to the last: keyboard
    #: navigation stays confined to the modal in both directions
    assert (
        js.evaluate(
            client,
            "(() => { const f = " + focusables_expr + ";"
            " return document.activeElement === f[f.length - 1]; })()",
        )
        is True
    )

    inputs.press_key(client, "Escape")
    closed = js.evaluate(
        client,
        "({hidden: document.getElementById('artifact-modal').hidden,"
        " bodyOpen: document.body.classList.contains('modal-open'),"
        " content: document.querySelector('.modal-content').innerHTML,"
        " focusRestored: document.activeElement === document.querySelector("
        + json.dumps(chip_selector)
        + ")})",
    )
    #: Escape closes, empties the content, releases the body and returns
    #: focus to the chip that opened the modal
    assert closed == {"hidden": True, "bodyOpen": False, "content": "", "focusRestored": True}
    if evidence_case is not None:
        evidence_case.attach_json(
            "modal-keyboard", {"total": total, "position": position, "closed": closed}
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.navigate-cockpit-views",
    proves=["The run view renders command timeline, JUnit tables and playable casts."],
)
def test_cockpit_run_view_lists_commands_timeline_and_casts(page, cockpit_report, evidence_case):
    """The Run view tells the story of the proof run: a proportional
    timeline of commands (failure in red), command and JUnit tables, log
    tails, and the casts section with its gate table and its xterm-playable
    chip."""
    client, _base = page
    _open_cockpit(client, cockpit_report, "/run", "!!document.querySelector('#app .run-timeline')")

    run_view = js.evaluate(
        client,
        "({bars: document.querySelectorAll('.run-timeline .tl-bar').length,"
        " badBars: document.querySelectorAll('.run-timeline .tl-bad').length,"
        " badTitle: document.querySelector('.run-timeline .tl-bad').title,"
        " commandRows: document.querySelectorAll('#app .table-wrap')[0]"
        ".querySelectorAll('tbody tr').length,"
        " commandText: document.querySelectorAll('#app .table-wrap')[0].textContent,"
        " suiteRows: document.querySelectorAll('#app .table-wrap')[1]"
        ".querySelectorAll('tbody tr').length,"
        " suiteText: document.querySelectorAll('#app .table-wrap')[1].textContent,"
        " headings: Array.from(document.querySelectorAll('#app h2'), n => n.textContent),"
        " castChip: !!document.querySelector('#app .badges .chip[title=\"asciinema\"]'),"
        " castText: document.querySelectorAll('#app .table-wrap')[3].textContent,"
        " tails: Array.from("
        "document.querySelectorAll('#app details summary'), n => n.textContent).join(' ')})",
    )
    #: the timeline draws one bar per command and paints the failure in
    #: red, with the detail on hover
    assert run_view["bars"] == 3 and run_view["badBars"] == 1
    assert "Pytest E2E Chrome" in run_view["badTitle"]
    #: the command table lists every command's proof with its log
    assert run_view["commandRows"] == 3
    assert "Ruff lint" in run_view["commandText"]
    assert ".proof/e2e-chrome.log" in run_view["commandText"]
    #: the three JUnit suites (unit, e2e, symfony) are aggregated
    assert run_view["suiteRows"] == 3
    assert "symfony" in run_view["suiteText"]
    #: the gate's casts section lists the generated cast and log tails
    #: stay accessible as a fallback
    assert "Demo casts" in run_view["headings"]
    assert run_view["castChip"] is True
    assert "cdpx-help" in run_view["castText"]
    assert "Log tails" in run_view["tails"]
    #: the failed command's log tail is indeed embedded in the view
    assert js.evaluate(
        client,
        "document.querySelector('#app').textContent.includes("
        + json.dumps(f"FAILED {COCKPIT_FAIL_NODEID}")
        + ")",
    )

    _click(client, '#app .badges .chip[title="asciinema"]')
    cast_modal = js.evaluate(
        client,
        "({hidden: document.getElementById('artifact-modal').hidden,"
        " xterm: !!document.querySelector('.modal-content .xterm')})",
    )
    #: the catalog cast opens from the Run view in the xterm player
    assert cast_modal == {"hidden": False, "xterm": True}
    _close_modal(client)
    if evidence_case is not None:
        evidence_case.attach_json("run-view", {"run": run_view, "cast_modal": cast_modal})


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.navigate-cockpit-views",
    proves=["CLI surface and validation matrix render from the embedded payload."],
)
def test_cockpit_cli_and_validation_views(page, cockpit_report, evidence_case):
    """The CLI view lists the 31 real subcommands with their feature
    attachment, and the Validation view renders the milestone matrix,
    coverage by module, risks and accepted unknowns."""
    client, _base = page
    _open_cockpit(
        client,
        cockpit_report,
        "/cli",
        "document.querySelector('#app h1')?.textContent === 'CLI surface and entrypoints'",
    )
    cli_view = js.evaluate(
        client,
        "({intro: document.querySelector('#app p').textContent,"
        " rows: document.querySelectorAll('#app tbody tr').length,"
        " body: document.querySelector('#app tbody').textContent,"
        " mapped: !!document.querySelector('#app tbody a[href=\"#/features/demo-checkout\"]')})",
    )
    #: the CLI contract (31 real subcommands, extracted from the real
    #: binary's help) is visible as-is in the cockpit
    assert cli_view["rows"] == 31
    assert "31 cdpx subcommands" in cli_view["intro"]
    assert "cdpx goto" in cli_view["body"] and "cdpx tabs" in cli_view["body"]
    #: each entrypoint shows its attachment: link to the feature when it
    #: exists, explicit mention otherwise
    assert cli_view["mapped"] is True
    assert "unattached" in cli_view["body"]

    _goto_route(
        client,
        "/validation",
        "document.querySelector('#app h1')?.textContent === 'Validation matrix'",
    )
    validation_view = js.evaluate(
        client,
        "({headings: Array.from(document.querySelectorAll('#app h2'), n => n.textContent),"
        " tables: document.querySelectorAll('#app .table-wrap table').length,"
        " text: document.querySelector('#app').textContent})",
    )
    #: the Validation view aligns its four panes, each with its table
    assert validation_view["headings"] == [
        "Proof by milestone",
        "Tests by module",
        "Risks and mitigations",
        "Accepted unknowns",
    ]
    assert validation_view["tables"] == 4
    #: matrix, coverage, risks and unknowns render the run's data
    assert "M9" in validation_view["text"]
    assert "demo_checkout" in validation_view["text"]
    assert "make proof fails without a binary." in validation_view["text"]
    assert "Loopback fixtures only." in validation_view["text"]
    if evidence_case is not None:
        evidence_case.attach_json(
            "cli-and-validation-views", {"cli": cli_view, "validation": validation_view}
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.navigate-cockpit-views",
    proves=["Project context renders and unknown routes fall back to a not-found view."],
)
def test_cockpit_project_view_and_unknown_route(page, cockpit_report, evidence_case):
    """The Project view renders mission, version, git/environment context
    and the docs/fixtures inventories; an unknown route lands on the
    "View not found" view that names the offending path instead of an
    empty page."""
    client, _base = page
    _open_cockpit(
        client,
        cockpit_report,
        "/project",
        "document.querySelector('#app h1')?.textContent === 'Project context'",
    )
    project_view = js.evaluate(
        client,
        "({mission: document.querySelector('#app .panel').textContent,"
        " lists: Array.from("
        "document.querySelectorAll('#app .two .panel'), n => n.textContent)})",
    )
    #: the mission panel aggregates mission, version, git branch and environment
    assert "Chrome DevTools Protocol" in project_view["mission"]
    assert "0.0-e2e" in project_view["mission"]
    assert "e2e-cockpit" in project_view["mission"]
    assert "Chrome/Chromium present" in project_view["mission"]
    #: the project's docs and fixtures are inventoried in two panels
    assert any("README.md" in item for item in project_view["lists"])
    assert any("tests/fixtures/index.html" in item for item in project_view["lists"])

    _goto_route(
        client,
        "/nowhere",
        "document.querySelector('#app h1')?.textContent === 'View not found'",
    )
    not_found = js.evaluate(
        client,
        "({crumb: document.querySelector('#app .crumbs').textContent,"
        " route: document.querySelector('#app p code').textContent})",
    )
    #: the unknown route is named in the fallback view, breadcrumb included
    assert not_found["route"] == "/nowhere"
    assert "Not found" in not_found["crumb"]
    if evidence_case is not None:
        evidence_case.attach_json(
            "project-view-and-not-found", {"project": project_view, "not_found": not_found}
        )


def test_navigate_and_read_title(page):
    """A direct CDP navigation loads the reference site and the page's JS
    context reflects the document actually loaded."""
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    #: the title read via Runtime proves the right page is loaded and executable
    assert js.evaluate(c, "document.title") == "cdpx fixtures — accueil"


def test_wait_for_late_spa_content(page):
    """wait_for genuinely waits for content injected late by an SPA instead
    of concluding on the first pass."""
    c, base = page
    nav.navigate(c, f"{base}/spa.html")
    res = nav.wait_for(c, "#late-content", timeout=5)
    #: the element is found and the measured delay proves a real wait
    #: (the fixture only injects the content after ~250 ms)
    assert res["found"] and res["elapsed_ms"] >= 250


def test_form_click_and_type(page):
    """The synthetic typing then click trigger the form's real logic: the
    final DOM contains the submitted value."""
    c, base = page
    nav.navigate(c, f"{base}/form.html")
    inputs.type_text(c, "#name", "Léo")
    inputs.click(c, "#submit-btn")
    #: the submit handler saw the typed value — the whole
    #: type/click/JS chain genuinely worked
    assert js.get_text(c, "#result")["text"] == "OK:Léo"


def test_rich_interactions_enforce_hit_test_and_clear_with_input_events(page):
    """The hit test denies clicks and typing on any non-actionable element
    (hidden, disabled, inert, covered...), and --clear empties the field via
    real input events that controlled frameworks can observe."""
    c, base = page
    nav.navigate(c, f"{base}/interactions-rich.html")

    for selector, reason in (
        ("#hidden-button", "not visible"),
        ("#disabled-button", "disabled"),
        ("#aria-disabled-button", "disabled"),
        ("#inert-button", "disabled"),
        ("#pointer-events-button", "disabled"),
        ("#covered-button", "covered"),
    ):
        #: each cause of non-actionability is denied with its precise reason, before the click
        with pytest.raises(inputs.ElementNotInteractable, match=reason):
            inputs.click(c, selector)

    snapshot = js.evaluate(c, "window.interactionFixture.snapshot()")
    #: the page confirms that none of the denied clicks leaked through to the handlers
    assert snapshot["clicks"] == {
        "hidden": 0,
        "disabled": 0,
        "ariaDisabled": 0,
        "inert": 0,
        "pointerEvents": 0,
        "covered": 0,
        "descendant": 0,
    }

    inputs.click(c, "#descendant-button")
    #: a click point that lands on a descendant of the selector stays a legitimate click
    assert js.evaluate(c, "window.interactionFixture.snapshot().clicks.descendant") == 1

    for selector, reason in (
        ("#hidden-button", "not visible"),
        ("#disabled-button", "disabled"),
        ("#descendant-button", "not editable"),
    ):
        #: typing applies the same guards, plus the denial of non-editable elements
        with pytest.raises(inputs.ElementNotInteractable, match=reason):
            inputs.type_text(c, selector, "must-not-be-typed")

    type_result = inputs.type_text(c, "#controlled-input", "fresh", clear=True)
    #: the typing succeeds and the typed value does not leak into the JSON output
    assert type_result["typed"] is True
    assert type_result["value_masked"] is True
    assert "fresh" not in json.dumps(type_result, ensure_ascii=False)
    snapshot = js.evaluate(c, "window.interactionFixture.snapshot()")
    #: the field and its controlled mirror see the new value, and --clear
    #: emitted the expected beforeinput/input instead of silently overwriting
    assert snapshot["input"] == "fresh"
    assert snapshot["mirror"] == "fresh"
    assert any(
        event["type"] == "beforeinput" and event["value"] == "legacy"
        for event in snapshot["inputEvents"]
    )
    assert any(
        event["type"] == "input" and event["value"] == "" for event in snapshot["inputEvents"]
    )

    for key in ("Home", "Delete", "End", "Space"):
        #: each special key is transmitted and acknowledged by the protocol
        assert inputs.press_key(c, key) == {"pressed": key}
    snapshot = js.evaluate(c, "window.interactionFixture.snapshot()")
    #: the final content proves the keys moved a real cursor and edited the field
    assert snapshot["input"] == "resh "
    assert snapshot["mirror"] == "resh "


def test_console_capture_real(page):
    """The windowed console capture observes both the logs AND the
    exceptions emitted by a real page, with an aggregated error count."""
    c, base = page
    c.send("Runtime.enable")
    nav.navigate(c, f"{base}/console.html")
    res = capture.console_capture(c, duration=1.0)
    texts = [e["text"] for e in res["entries"]]
    #: the log planted by the fixture is captured and the uncaught exception counts as an error
    assert any("fixture-log" in t for t in texts)
    assert res["errors"] >= 1


def test_network_capture_real(page):
    """The network capture observes a page's real traffic: HTTP failures
    are aggregated and each request is rendered individually."""
    c, base = page
    res = net.capture(c, f"{base}/network.html", settle=1.0)
    #: the fixture's deliberate 500 call is counted as a failure
    assert res["summary"]["errors_4xx_5xx"] >= 1  # /api/status/500
    urls = [r.get("url", "") for r in res["requests"]]
    #: the per-request detail makes individual API calls traceable
    assert any("/api/json" in u for u in urls)


def test_profiler_fixture_real(page):
    """The Symfony profiler reader extracts panel metrics via a real
    page-context fetch, without ever leaking the profiler's token into the
    output."""
    # real page-context fetch: Chrome goes and fetches the fixture server's
    # HTML panels and the parsers extract the fixed values from them.
    c, base = page
    res = dev.profiler(c, f"{base}/api/profiler-sim")
    #: the token's presence is reported but its value appears nowhere
    assert res["token_present"] is True
    assert "token" not in res and "fixed-token" not in json.dumps(res)
    #: each panel (db, cache, router, exception, logger) is parsed with the
    #: fixed values served by the fixture
    assert res["profiler_status"] == 200
    assert res["panels"]["db"]["queries"] == 6
    assert res["panels"]["db"]["duplicates"] == 4
    assert res["panels"]["cache"]["hits"] == 3
    assert res["panels"]["router"]["route"] == "scenario_profiler"
    assert res["panels"]["exception"]["raised"] is False
    assert res["panels"]["logger"]["deprecations"] == 2


def test_dom_diff_real(page):
    """dom-diff executes the enclosed action and makes the DOM change it
    triggers readable."""
    c, base = page
    nav.navigate(c, f"{base}/form.html")
    inputs.type_text(c, "#name", "Léo")
    res = dev.dom_diff(c, ClickAction("#submit-btn"))
    #: the diff materializes the mutation triggered by the click (transition to submitted state)
    assert res["changed"] is True
    assert any("submitted" in line for line in res["diff"])


def test_a11y_and_frame_real(page):
    """A real page's accessibility tree is usable and frame_text reaches
    content inside a child iframe."""
    c, base = page
    nav.navigate(c, f"{base}/iframe.html")
    tree = diagnostics.a11y(c)
    #: Chrome exposes a non-empty a11y tree for the host page
    assert tree["count"] > 0
    #: the text read does come from the child document, not the host page
    assert frames.frame_text(c, "#child-marker")["text"] == "Contenu de l'iframe"


def test_coverage_real(page):
    """CSS coverage measured on a real page is consistent: used and unused
    rules split the total exactly."""
    c, base = page
    res = diagnostics.coverage(c, f"{base}/coverage.html")
    #: the fixture exposes at least one sheet whose rules are genuinely exercised
    assert res["count"] >= 1
    assert res["css"]["rules"] >= 1
    assert res["css"]["used"] >= 1
    #: the used/unused partition is exact — no rule lost or counted twice
    assert res["css"]["used"] + res["css"]["unused"] == res["css"]["rules"]


def test_intercept_real_fulfill_block_continue(page):
    """Network interception applies the three verdicts (rewrite to 204,
    block, pass through) on real traffic, and the page observes exactly the
    altered responses."""
    c, base = page
    res = interception.intercept_goto(
        c,
        f"{base}/intercept.html",
        rules=[
            "*api/status/500* => 204",
            "*api/slow* => block",
        ],
        settle=1.0,
    )
    actions = {hit["action"] for hit in res["hits"]}
    #: all three interception verdicts were exercised during the navigation
    assert {"204", "block", "continue"}.issubset(actions)
    deadline = time.monotonic() + 3
    text = ""
    while time.monotonic() < deadline:
        text = js.get_text(c, "#intercept-result")["text"] or ""
        if "pending" not in text:
            break
        time.sleep(0.1)
    #: the page itself saw the passed-through response untouched, the
    #: response rewritten to 204 and the blocked call ending in an error
    assert "/api/json:200" in text
    assert "/api/status/500:204" in text
    assert "/api/slow?ms=120:ERR" in text


def test_vitals_real_with_interaction(page):
    """The Web Vitals (LCP, CLS, INP) are measured on a real page, the INP
    being triggered by a synthetic click whose trace the page keeps."""
    c, base = page
    res = diagnostics.vitals(c, f"{base}/vitals.html", click_selector="#inp-button", settle=1.0)
    #: all three metrics are present and plausible (never negative)
    assert set(res) == {"url", "lcp", "cls", "inp"}
    assert res["lcp"] >= 0 and res["cls"] >= 0 and res["inp"] >= 0
    #: the interaction that feeds the INP genuinely reached the page
    assert js.evaluate(c, "document.body.dataset.clicked") == "1"


@pytest.mark.scenario(
    feature="seo-performance-accessibility",
    journey="audit-seo-rendered-dom",
    scenario_id="seo-performance-accessibility.audit-rendered-seo-and-a11y",
    proves=["SEO audit surfaces edge-case findings from the rendered DOM."],
)
def test_seo_edge_real(page):
    """The SEO audit detects edge cases: pixel-width estimate of the title,
    duplicated h1s, invalid JSON-LD and incomplete Product."""
    c, base = page
    nav.navigate(c, f"{base}/seo-edge.html")
    res = audit.seo(c)
    #: the title's width is estimated in pixels, beyond a simple character count
    assert res["title_px_estimate"] > 0
    #: each trap set by the fixture surfaces as an explicit, actionable finding
    assert "duplicate h1: produit dupliqué" in res["findings"]
    assert "invalid JSON-LD" in res["findings"]
    assert "incomplete Product JSON-LD (sku or name required)" in res["findings"]


def test_origin_guard_cli_real(managed_cli_session, fixtures_http, evidence_case):
    """The supervised session's origin guard blocks any navigation outside
    the authorized origins, via the CLI contract's error channel."""
    manifest, path = managed_cli_session
    cli_json(managed_cli_session, "goto", f"{fixtures_http.base_url}/index.html")
    proc = run_cli(manifest, path, "goto", "https://blocked.example/")
    attach_cli_run(evidence_case, "goto-origin-rejected", proc)
    with CDPClient(manifest.websocket_url, timeout=10) as client:
        attach_screenshot(evidence_case, client, "origin-guard-final")
    #: navigating to a forbidden origin fails with a runtime error and an explicit denial
    assert proc.returncode == 1
    assert "origin rejected" in proc.stderr


def test_metrics_real(page):
    """Chrome's performance metrics are collected on a real page with live
    values, not complacent zeros."""
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    res = audit.metrics(c)
    #: non-zero DOM nodes, documents and JS heap prove a real collection, not a stub
    assert res["Nodes"] > 0 and res["Documents"] > 0
    assert res["JSHeapUsedSize"] > 0


def test_pdf_real(page, tmp_path):
    """Printing via CDP produces a real, non-trivial PDF document on
    disk."""
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    dest = tmp_path / "page.pdf"
    res = capture.pdf(c, str(dest))
    #: plausible size and %PDF- signature attest to a genuinely printed document
    assert res["bytes"] > 1000
    assert dest.read_bytes().startswith(b"%PDF-")


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="replay-flow",
    scenario_id="orchestration-control.orchestrate-replay-and-emulation",
    proves=[
        "Record then replay reconstructs a bounded flow and halts at the first divergence.",
    ],
)
def test_record_replay_real(chrome, fixtures_http, evidence_case, tmp_path, monkeypatch):
    """A recorded flow acts immediately and then replays in full on a
    blank tab; an altered journal triggers a detected divergence and a
    clean stop at the right event."""
    journal = tmp_path / "session.ndjson"
    base = fixtures_http.base_url
    context = OrchestrationContext.from_origins("http://127.0.0.1:*")
    monkeypatch.setenv("FORM_NAME", "Léo")
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            recording.record(
                c,
                str(journal),
                GotoAction(f"{base}/form.html"),
                context=context,
            )
            recording.record(
                c,
                str(journal),
                TypeAction("#name", "@env:FORM_NAME", clear=True),
                context=context,
            )
            recording.record(
                c,
                str(journal),
                ClickAction("#submit-btn"),
                context=context,
            )
            #: recording is not passive: each step acted on the page as it captured it
            assert js.get_text(c, "#result")["text"] == "OK:Léo"  # record really DID act
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])
    # full replay on a blank tab: the flow reconstructs itself
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            res = recording.replay(c, str(journal), context=context)
            #: the replay reconstructs the three steps on its own and reaches the same final DOM
            assert res["ok"] is True and res["played"] == 3
            assert js.get_text(c, "#result")["text"] == "OK:Léo"
            attach_screenshot(evidence_case, c, "replay-final")
            if evidence_case is not None:
                # Intact replayable journal (.ndjson typed logs/internal): the
                # @env secret never appears in it, only the reference is persisted.
                evidence_case.attach_file(journal, "replayable-journal-ndjson", "logs")
            # altered journal (selector gone) -> divergence, clean stop
            journal.write_text(
                journal.read_text().replace("#submit-btn", "#gone"), encoding="utf-8"
            )
            broken = recording.replay(c, str(journal), context=context)
            #: the divergence is localized to the altered event and the
            #: replay stops right there instead of continuing blindly
            assert broken["ok"] is False and broken["played"] == 2
            assert broken["divergence"].startswith("event 2:")
            if evidence_case is not None:
                # Divergence result: clean stop at the altered event (played=2),
                # readable proof of refusing to replay blindly after a broken journal.
                evidence_case.attach_json("replay-divergence", broken)
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])


def test_emulate_composed_action_real(chrome, fixtures_http, evidence_case):
    """Mobile emulation applied in the same CDP connection as the composed
    action is visible to the page during the goto (device and user-agent)."""
    # Acting under emulation = an action in the SAME connection (the
    # overrides die with it): the page sees the mobile device during the goto.
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            emulation.emulate(c, "mobile")
            result = actions.run_action(c, GotoAction(f"{fixtures_http.base_url}/index.html"))
            #: the page loaded under emulation sees the mobile preset's screen and user-agent
            assert result["ok"] is True
            assert js.evaluate(c, "screen.width") == 390
            assert "cdpx-mobile" in js.evaluate(c, "navigator.userAgent")
            attach_screenshot(evidence_case, c, "mobile-final")
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])


def test_emulate_mobile_and_reset_real(chrome, fixtures_http, evidence_case):
    """Two properties of emulate proven against real Chrome: --reset
    restores BOTH device AND user-agent within the connection, and the
    overrides die with the CDP connection — an isolated invocation does not
    pollute the page."""
    # Semantics proven against real Chrome:
    # 1. intra-connection, `--reset` restores BOTH device AND user-agent
    #    (historical bug: the mobile preset's UA survived the reset);
    # 2. emulation overrides die with the CDP connection — an isolated cdpx
    #    invocation therefore does NOT leave the page emulated behind it
    #    (hence the composed form `emulate <preset> -- <action>`).
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            nav.navigate(c, f"{fixtures_http.base_url}/index.html")
            initial = js.evaluate(c, "screen.width")
            emulation.emulate(c, "mobile")
            #: the mobile preset is effective on the page's side, screen and user-agent included
            assert js.evaluate(c, "screen.width") == 390
            assert "cdpx-mobile" in js.evaluate(c, "navigator.userAgent")
            emulation.emulate(c, reset=True)
            #: --reset restores both dimensions, including the UA (historical regression)
            assert js.evaluate(c, "screen.width") == initial
            assert "cdpx-mobile" not in js.evaluate(c, "navigator.userAgent")
            emulation.emulate(c, "mobile")  # reapply the override, then the connection closes
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            #: after the connection closes, the reapplied override is gone:
            #: no phantom emulation survives an isolated invocation
            assert js.evaluate(c, "screen.width") == initial  # died with the connection
            attach_screenshot(evidence_case, c, "final")
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])


def materialize_scenario(template: str, base_url: str, tmp_path: Path) -> Path:
    src = SCENARIO_FIXTURES / template
    dest = tmp_path / template
    dest.write_text(src.read_text(encoding="utf-8").replace("__BASE_URL__", base_url), "utf-8")
    return dest


def run_scenario_cli(
    session: tuple[SessionManifest, Path],
    scenario: Path,
    *,
    timeout: float = 10.0,
) -> tuple[int, dict, str]:
    manifest, path = session
    proc = run_cli(
        manifest,
        path,
        "--timeout",
        str(timeout),
        "scenario",
        "run",
        str(scenario),
        "--settle",
        "0.5",
        timeout=max(timeout * 8, 20),
    )
    payload = json.loads(proc.stdout) if proc.stdout else {}
    return proc.returncode, payload, proc.stderr


def attach_scenario_run(evidence_case, result: dict, label: str) -> None:
    if evidence_case is None:
        return
    evidence_case.attach_json(label, result, f"{label}.json")
    for artifact in result.get("artifacts", []):
        path = Path(artifact["path"])
        if path.exists():
            evidence_case.attach_file(
                path,
                f"{label}-{artifact['label']}-{artifact['type']}",
                artifact["type"],
            )


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="scenario-run",
    scenario_id="orchestration-control.run-declarative-business-scenario",
    proves=[
        "A YAML scenario drives a real browser through form navigation and interaction.",
        "Checkpoint and final artifacts are collected as proof files.",
    ],
)
def test_declarative_scenario_static_form_real(
    managed_cli_session, fixtures_http, tmp_path, evidence_case, monkeypatch
):
    """A declarative YAML scenario drives a real browser (navigation +
    form) to a pass verdict, collecting the checkpoint and end-of-flow
    artifacts as proof."""
    monkeypatch.setenv("E2E_FORM_NAME", "Leo")
    scenario = materialize_scenario("static_form_pass.yml", fixtures_http.base_url, tmp_path)
    code, result, err = run_scenario_cli(managed_cli_session, scenario)

    attach_scenario_run(evidence_case, result, "static-form-scenario")
    #: the business scenario ends with the expected verdict, with diagnostics attached on failure
    assert code == 0, f"stderr={err}\nresult={json.dumps(result, ensure_ascii=False, indent=2)}"
    assert result["verdict"] == "pass"
    #: the checkpoint and end-of-flow visual proofs are indeed collected
    assert any(artifact["label"] == "form_page" for artifact in result["artifacts"])
    assert any(artifact["label"] == "final" for artifact in result["artifacts"])


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="scenario-run",
    scenario_id="orchestration-control.run-declarative-business-scenario",
    proves=[
        "A YAML scenario returns one fail verdict when console and network assertions fail.",
        "Failure evidence still includes checkpoint and final artifacts.",
    ],
)
def test_declarative_scenario_static_observability_fail_real(
    managed_cli_session, fixtures_http, tmp_path, evidence_case
):
    """When the observability assertions (console, network) fail, the
    scenario renders a single fail verdict with identifiable findings,
    without breaking proof collection."""
    scenario = materialize_scenario(
        "static_observability_fail.yml", fixtures_http.base_url, tmp_path
    )
    code, result, _ = run_scenario_cli(managed_cli_session, scenario)

    attach_scenario_run(evidence_case, result, "static-observability-scenario")
    #: the business failure uses the runtime error channel with an explicit verdict
    assert code == 1
    assert result["verdict"] == "fail"
    #: each violated assertion produces its coded finding, usable by a machine
    assert {finding["code"] for finding in result["findings"]} >= {
        "assertion_no_console_errors",
        "assertion_network_errors_max",
    }


@pytest.mark.scenario(
    feature="seo-performance-accessibility",
    journey="audit-seo-rendered-dom",
    scenario_id="seo-performance-accessibility.audit-rendered-seo-and-a11y",
    proves=["SEO audit stays clean on a healthy page and flags a broken one."],
)
def test_seo_audit_real(page):
    """The SEO audit returns a clean report on a healthy page (JSON-LD
    parsing included) and flags the defects of a broken one."""
    c, base = page
    nav.navigate(c, f"{base}/seo.html")
    res = audit.seo(c)
    #: the healthy reference page triggers no false positive and its JSON-LD is parsed correctly
    assert res["findings"] == []
    assert res["jsonld"][0]["sku"] == "FIX-001"
    nav.navigate(c, f"{base}/seo-broken.html")
    broken = audit.seo(c)
    #: the same audit on the broken page detects the duplicate h1
    assert "2 h1 (expected: 1)" in broken["findings"]


def test_cookies_and_storage_real(page):
    """Cookies set by the page are readable via CDP and localStorage comes
    out masked by default, unmasking staying an explicit choice."""
    c, base = page
    nav.navigate(c, f"{base}/storage.html")
    cookies = state.get_cookies(c, show_values=True)["cookies"]
    #: the cookie created in JavaScript by the page is visible via CDP
    assert any(ck["name"] == "jsCookie" for ck in cookies)
    storage = state.get_storage(c, "local")
    #: by default the storage value is masked, with the flag that announces it
    assert storage["entries"].get("cdpx-key") == "***"
    assert storage["values_masked"] is True
    shown = state.get_storage(c, "local", show_values=True)
    #: explicit unmasking returns the real value set by the fixture
    assert shown["entries"].get("cdpx-key") == "cdpx-value"


def test_screenshot_real(page, tmp_path, evidence_case):
    """The screenshot command writes a real, non-trivial PNG from a page
    loaded in Chrome."""
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    out = tmp_path / "e2e.png"
    res = capture.screenshot(c, str(out))
    if evidence_case is not None:
        evidence_case.attach_screenshot(out, "screenshot-command")
    #: the size and PNG signature attest to an image genuinely captured
    assert res["bytes"] > 1000
    assert out.read_bytes().startswith(b"\x89PNG")


def test_full_page_screenshot_captures_long_page(page, tmp_path, evidence_case):
    """The full-page capture embeds content beyond the viewport: it is
    strictly heavier than the standard capture of the same long page."""
    c, base = page
    nav.navigate(c, f"{base}/long.html")
    normal = tmp_path / "normal.png"
    full = tmp_path / "full.png"
    normal_res = capture.screenshot(c, str(normal))
    full_res = capture.screenshot(c, str(full), full_page=True)
    if evidence_case is not None:
        evidence_case.attach_screenshot(normal, "normal-screenshot")
        evidence_case.attach_screenshot(full, "full-page-screenshot")
    #: the full-page version's extra weight proves the offscreen content is
    #: indeed included, and the file stays a valid PNG
    assert full_res["full_page"] is True
    assert full_res["bytes"] > normal_res["bytes"]
    assert full.read_bytes().startswith(b"\x89PNG")


def test_json_endpoint_reachable_from_page(page):
    """The fixture server is reachable from the page's context: a real
    same-origin fetch succeeds and returns the expected JSON."""
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    raw = js.evaluate(c, f"fetch('{base}/api/json').then(r => r.text())", await_promise=True)
    #: the response parsed from the page proves the full fetch -> fixture server chain
    assert json.loads(raw)["ok"] is True
