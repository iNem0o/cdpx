import json
import stat
from datetime import datetime
from pathlib import Path

import pytest

from cdpx import proof
from cdpx.artifacts import ArtifactError
from cdpx.cli import build_parser
from cdpx.security.redaction import RedactionContext


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_repo_env_is_allowlisted_and_excludes_credentials(monkeypatch):
    """L'environnement transmis aux commandes de preuve est construit par
    allowlist: les identifiants ambiants du shell n'y entrent jamais, seules
    les variables utiles et le réglage de rétention passent."""
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/tmp/home")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("CDPX_TEST_SECRET", "cdpx-secret")
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "30")

    env = proof._repo_env()

    #: seules les variables allowlistées survivent, PYTHONPATH garanti aux sous-processus
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/tmp/home"
    assert "PYTHONPATH" in env
    #: les identifiants présents dans le shell ne peuvent pas fuiter vers les logs de preuve
    assert "GITHUB_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "CDPX_TEST_SECRET" not in env
    #: le réglage de rétention, non sensible, est explicitement laissé passer
    assert env["CDPX_PROOF_RETENTION_DAYS"] == "30"


def test_run_evidence_redacts_command_and_output_and_uses_private_mode(
    tmp_path, monkeypatch, evidence_case
):
    """Une exécution d'évidence redacte sa sortie avant l'écriture disque et
    protège le log en mode privé: la valeur secrète n'atteint jamais un
    fichier lisible par d'autres."""
    secret = "proof-secret-123"
    context = RedactionContext.from_secrets([secret])

    class Completed:
        returncode = 0
        stdout = f"token={secret}\nBearer abcdefghijk"

    monkeypatch.setattr(proof.subprocess, "run", lambda *args, **kwargs: Completed())
    log = tmp_path / "logs" / "command.log"

    proof.run_evidence(
        "secret",
        "Secret",
        ["tool", secret],
        log,
        env={"PATH": "/usr/bin"},
        redaction_context=context,
    )

    contents = log.read_text(encoding="utf-8")
    #: la valeur secrète est remplacée par le marqueur de redaction avant d'atteindre le disque
    assert secret not in contents
    assert "***" in contents
    #: répertoire 0700 et log 0600: l'évidence brute reste privée au propriétaire
    assert mode(log.parent) == 0o700
    assert mode(log) == 0o600

    if evidence_case is not None:
        # Extrait ciblé sur le marqueur *** du log DÉJÀ redacté sur disque: la
        # preuve visuelle montre la censure sans jamais transporter le secret.
        excerpt = evidence_case.attach_log_excerpt(
            log,
            "Log d'évidence redacté — marqueur *** en place du secret",
            pattern=r"\*\*\*",
        )
        #: l'artefact de preuve produit ne contient jamais la valeur secrète, seulement ***
        assert secret not in Path(excerpt["path"]).read_text(encoding="utf-8")


def test_build_shareable_proof_allowlists_sanitized_text_and_excludes_opaque(
    tmp_path, evidence_case
):
    """Le staging partageable n'embarque que les textes sanitisés allowlistés;
    les binaires opaques restent hors staging et sont classés non uploadables
    dans le manifest privé."""
    proof_dir = tmp_path / ".proof"
    report = '<script>const graph={data:[1,2]};const icon="data:image/png;base64,abc";</script>'
    proof._write_private_text(proof_dir / "proof-report.html", report)
    proof._write_private_text(proof_dir / "validation-summary.json", '{"ok": true}\n')
    proof._write_private_bytes(proof_dir / "evidence" / "shot.png", b"\x89PNG\r\n")

    staging = proof.build_shareable_proof(
        proof_dir,
        canaries=["never-present"],
        ttl=7200,
        pre_redacted_paths={"proof-report.html"},
    )

    #: seuls le rapport et le résumé passent en staging, la capture binaire est retenue
    assert (staging / ".proof" / "proof-report.html").exists()
    assert (staging / ".proof" / "validation-summary.json").exists()
    assert not (staging / ".proof" / "evidence" / "shot.png").exists()
    public_manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    #: le manifest public annonce une expiration future et n'autorise que de l'uploadable
    assert public_manifest["expires_at"] > public_manifest["created_at"]
    assert all(item["upload_allowed"] for item in public_manifest["artifacts"])
    #: le staging garde des permissions privées, rien n'est élargi avant l'upload
    assert mode(staging) == 0o700
    assert mode(staging / "manifest.json") == 0o600
    assert mode(staging / ".proof" / "proof-report.html") == 0o600
    #: le rapport pré-redacté est copié tel quel, sans re-sanitisation destructive
    assert (staging / ".proof" / "proof-report.html").read_text(encoding="utf-8") == report
    private_manifest = json.loads(
        (proof_dir / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    screenshot = next(
        item for item in private_manifest["artifacts"] if item["path"].endswith("shot.png")
    )
    #: le manifest privé trace la décision d'exclusion du binaire, auditable après coup
    assert screenshot["classification"] == "opaque-restricted"
    assert screenshot["upload_allowed"] is False

    if evidence_case is not None:
        # Les deux manifests matérialisent la décision d'allowlist: le public
        # ne liste que l'uploadable, le privé garde la trace de l'exclusion.
        evidence_case.attach_json(
            "Manifest public partageable (allowlist)",
            public_manifest,
            filename="public-manifest.json",
        )
        evidence_case.attach_json(
            "Manifest privé (exclusion du binaire opaque)",
            private_manifest,
            filename="private-manifest.json",
        )


def test_build_shareable_proof_fails_closed_on_canary(tmp_path):
    """La détection d'un canari dans un artefact fait échouer la construction
    fermée: aucun staging partiel ne survit à l'échec."""
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "unsafe.log", "leaked-canary")

    #: le canari présent dans un artefact bloque la construction et est nommé dans l'erreur
    with pytest.raises(ArtifactError, match="canary"):
        proof.build_shareable_proof(proof_dir, canaries=["leaked-canary"])

    #: échec fermé: aucun résidu partageable n'a été créé
    assert not (proof_dir / "shareable").exists()


def test_pre_redacted_report_still_fails_closed_on_canary(tmp_path):
    """Déclarer un fichier pré-redacté ne l'exempte pas du contrôle canari:
    la vérification anti-fuite reste systématique."""
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>leaked-canary</p>")

    #: même un chemin déclaré pré-redacté est scanné et bloque la construction
    with pytest.raises(ArtifactError, match="canary"):
        proof.build_shareable_proof(
            proof_dir,
            canaries=["leaked-canary"],
            pre_redacted_paths={"proof-report.html"},
        )

    #: l'échec fermé n'a laissé aucun staging résiduel
    assert not (proof_dir / "shareable").exists()


def test_build_shareable_proof_uses_validated_environment_retention(tmp_path, monkeypatch):
    """La rétention lue dans l'environnement pilote réellement l'expiration
    inscrite au manifest partageable."""
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "30")
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>safe</p>")

    staging = proof.build_shareable_proof(proof_dir)

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    created = datetime.fromisoformat(manifest["created_at"])
    expires = datetime.fromisoformat(manifest["expires_at"])
    #: l'écart created/expires reflète exactement la rétention demandée via l'environnement
    assert (expires - created).days == 30


def test_build_shareable_proof_rejects_invalid_environment_retention(tmp_path, monkeypatch):
    """Une rétention non numérique est rejetée avec une erreur nommant la
    variable fautive, plutôt que remplacée en silence par un défaut."""
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "unbounded")
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>safe</p>")

    #: la valeur invalide est refusée et l'erreur cible la variable pour un diagnostic direct
    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        proof.build_shareable_proof(proof_dir)

    #: la validation intervient avant toute écriture: pas de staging malgré un rapport sain
    assert not (proof_dir / "shareable").exists()


def test_generate_rejects_invalid_retention_before_replacing_existing_proof(tmp_path, monkeypatch):
    """generate() valide la rétention avant de purger .proof: une
    configuration invalide ne détruit jamais la preuve existante."""
    proof_dir = tmp_path / ".proof"
    proof_dir.mkdir()
    marker = proof_dir / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "0")

    #: la rétention nulle est refusée dès l'entrée de generate()
    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        proof.generate()

    #: la preuve précédente est intacte: aucune purge avant validation réussie
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_project_unknowns_describe_private_screenshot_scope():
    """Le packet risques/inconnues documente honnêtement les captures
    visuelles: privées, exclues du partage, sans prétendre qu'elles ne sont
    pas conservées."""
    packet = proof.build_project_risks_and_unknowns()
    screenshot = next(
        item for item in packet["unknowns"] if item["item"] == "Portée des captures visuelles"
    )

    #: le texte situe les captures, affirme leur exclusion du partage et évite
    #: la formulation trompeuse sur leur non-conservation
    assert ".proof/evidence/" in screenshot["why"]
    assert "exclues du staging partageable" in screenshot["why"]
    assert "sans le conserver" not in screenshot["why"]


def empty_scenario_evidence():
    suites = {"unit": [], "integration": [], "e2e": []}
    return {"suites": suites, "files": [], "totals": proof.scenario_totals(suites)}


def generated_casts():
    return [
        {"id": cast_id, "path": f".proof/{cast_id}.cast", "bytes": 64, "status": "generated"}
        for cast_id, _argv in proof.CAST_COMMANDS
    ]


def _evidence_with_artifacts(artifacts):
    suites = {
        "unit": [
            {
                "nodeid": "tests/test_demo.py::test_inline",
                "suite": "unit",
                "title": "inline",
                "status": "passed",
                "artifacts": artifacts,
            }
        ],
        "integration": [],
        "e2e": [],
    }
    return {"suites": suites, "files": [], "totals": proof.scenario_totals(suites)}


def test_inline_scenario_artifacts_inlines_small_text_and_excerpts_large(tmp_path):
    """L'inlining embarque les petits textes entiers, tronque honnêtement les
    gros, refuse les binaires, signale les chemins illisibles et ne mute
    jamais l'évidence d'entrée."""
    small = tmp_path / "run.txt"
    small.write_text("$ cdpx version\nok\n", encoding="utf-8")
    large = tmp_path / "big.log"
    large.write_text("\n".join(f"line-{index}" for index in range(2000)), encoding="utf-8")
    shot = tmp_path / "final.png"
    shot.write_bytes(b"\x89PNG\r\n")

    evidence = _evidence_with_artifacts(
        [
            {"type": "command", "label": "run", "path": str(small), "excerpt": ""},
            {"type": "logs", "label": "big", "path": str(large)},
            {"type": "screenshot", "label": "final", "path": str(shot)},
            {"type": "json", "label": "gone", "path": str(tmp_path / "missing.json")},
        ]
    )

    inlined = proof.inline_scenario_artifacts(evidence)
    command, logs, screenshot, missing = inlined["suites"]["unit"][0]["artifacts"]

    #: le petit texte voyage entier dans le payload
    assert command["inline_content"].startswith("$ cdpx version")
    assert command["truncated"] is False

    #: le gros log devient un extrait tête+queue honnêtement tronqué
    assert "inline_content" not in logs
    assert logs["inline_skipped"] == "taille"
    assert logs["truncated"] is True
    assert logs["excerpt"].startswith("line-0")
    assert "lignes tronquées" in logs["excerpt"]

    #: le binaire n'est jamais inliné (il resterait dans le HTML partageable)
    assert "inline_content" not in screenshot and "inline_skipped" not in screenshot

    #: un chemin illisible est signalé, pas fatal
    assert missing["inline_skipped"] == "illisible"

    #: les artefacts d'entrée ne sont pas mutés
    assert "inline_content" not in evidence["suites"]["unit"][0]["artifacts"][0]


def test_inline_scenario_artifacts_respects_global_budget(tmp_path):
    """Le budget global d'inlining borne le poids du rapport: les premiers
    artefacts voyagent entiers, les suivants dégradent en extrait marqué."""
    files = []
    for index in range(3):
        path = tmp_path / f"part-{index}.txt"
        path.write_text("x" * 1000, encoding="utf-8")
        files.append({"type": "command", "label": f"part-{index}", "path": str(path)})

    inlined = proof.inline_scenario_artifacts(_evidence_with_artifacts(files), budget=2500)
    first, second, third = inlined["suites"]["unit"][0]["artifacts"]

    #: tant que le budget le permet, le contenu complet voyage
    assert "inline_content" in first and "inline_content" in second
    #: budget épuisé => extrait marqué, jamais de contenu silencieusement absent
    assert third["inline_skipped"] == "budget"
    assert third["truncated"] is True and third["excerpt"]


def test_strip_inline_content_keeps_excerpts_but_drops_bodies(tmp_path):
    """La version allégée destinée au JSON de synthèse retire les corps
    inlinés tout en gardant les métadonnées d'extrait, sans toucher la copie
    servant au rendu HTML."""
    path = tmp_path / "run.txt"
    path.write_text("payload\n", encoding="utf-8")
    inlined = proof.inline_scenario_artifacts(
        _evidence_with_artifacts([{"type": "command", "label": "run", "path": str(path)}])
    )

    lean = proof._strip_inline_content(inlined)

    artifact = lean["suites"]["unit"][0]["artifacts"][0]
    #: le corps disparaît du payload allégé, les métadonnées de troncature restent
    assert "inline_content" not in artifact
    assert artifact["truncated"] is False
    #: la version inlinée reste intacte pour le rendu HTML
    assert inlined["suites"]["unit"][0]["artifacts"][0]["inline_content"] == "payload\n"


def test_render_html_size_stays_bounded():
    """Le rapport HTML complet reste sous un plafond de taille connu: toute
    dérive du shell/CSS/JS au-delà de la marge Mermaid casse le build."""
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )
    # Mermaid vendorisé ~3,5 Mo; le shell/CSS/JS du cockpit doit rester marginal.
    #: au-delà du plafond, un asset a grossi sans justification: le portail le bloque
    assert len(proof.render_html(summary)) < 4_500_000


def test_load_scenario_evidence_accepts_legacy_v1_payloads(tmp_path):
    """Un *-scenarios.json v1 (sans clé schema ni intent) reste lisible tel
    quel: la tolérance des lecteurs évite tout migrateur."""
    # Un *-scenarios.json v1 (sans clé schema, sans intent/assertions) doit
    # rester lisible: les lecteurs sont tolérants, aucun migrateur requis.
    legacy = {
        "suite": "unit",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "count": 1,
        "scenarios": [
            {
                "nodeid": "tests/test_demo.py::test_legacy",
                "suite": "unit",
                "title": "legacy",
                "status": "passed",
                "artifacts": [],
            }
        ],
    }
    (tmp_path / "unit-scenarios.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )

    evidence = proof.load_scenario_evidence(tmp_path)

    #: le payload legacy est compté et restitué comme un scénario moderne
    assert evidence["totals"]["unit"] == 1
    assert evidence["suites"]["unit"][0]["nodeid"] == "tests/test_demo.py::test_legacy"


def test_parse_junit_extracts_counts_and_cases(tmp_path, evidence_case):
    """Le parseur JUnit restitue les compteurs agrégés et le détail par cas
    (statut, message d'échec) depuis le XML produit par pytest."""
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="3" failures="1" errors="0" skipped="1" time="1.25">
    <testcase classname="tests.test_ok" name="test_passes" time="0.1" />
    <testcase classname="tests.test_bad" name="test_fails" time="0.2">
      <failure message="assertion failed">details</failure>
    </testcase>
    <testcase classname="tests.test_skip" name="test_skips" time="0.0">
      <skipped message="no chrome" />
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    parsed = proof.parse_junit(junit)

    #: les compteurs dérivés (dont passed) sont cohérents avec le XML source
    assert parsed["tests"] == 3
    assert parsed["passed"] == 1
    assert parsed["failures"] == 1
    assert parsed["skipped"] == 1
    #: chaque cas conserve statut et message d'échec pour le rendu du cockpit
    assert parsed["cases"][1]["status"] == "failed"
    assert parsed["cases"][1]["message"] == "assertion failed"

    if evidence_case is not None:
        # Entrée/sortie côte à côte: le XML source brut et le dict dérivé, pour
        # vérifier à l'œil que compteurs et cas correspondent.
        evidence_case.attach_text(
            "XML JUnit source (entrée du parseur)",
            junit.read_text(encoding="utf-8"),
            filename="junit-source.xml",
        )
        evidence_case.attach_json(
            "Dict parsé par parse_junit (sortie)",
            parsed,
            filename="parsed-junit.json",
        )


def test_parse_junit_reports_malformed_xml(tmp_path):
    """Un XML tronqué ne fait pas planter la collecte: le parseur signale
    l'erreur de parsing tout en confirmant l'existence du fichier."""
    junit = tmp_path / "junit.xml"
    junit.write_text("<testsuite>", encoding="utf-8")

    parsed = proof.parse_junit(junit)

    #: fichier présent mais illisible: zéro test compté et erreur explicite, jamais d'exception
    assert parsed["exists"] is True
    assert parsed["tests"] == 0
    assert parsed["parse_error"]


def test_parse_help_commands_uses_captured_argparse_help():
    """Le catalogue CLI du rapport provient de l'aide argparse réelle: les
    sous-commandes phares y figurent avec leur texte d'aide."""
    help_text = build_parser().format_help()

    commands = proof.parse_help_commands(help_text)

    names = {command["name"] for command in commands}
    #: retrouver les primitives clés prouve que l'extraction lit la vraie section subcommands
    assert {"goto", "seo", "vitals", "replay"}.issubset(names)
    assert any(command["help"] for command in commands if command["name"] == "seo")


def test_build_summary_preserves_historical_artifact_keys():
    """Les clés historiques du summary restent stables: les chemins publiés
    sont les emplacements canoniques, pas ceux des entrées passées."""
    unit = {
        "tests": 2,
        "passed": 2,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "cases": [],
    }
    e2e = {
        "tests": 1,
        "passed": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 1,
        "cases": [],
    }
    command = proof.CommandEvidence(
        id="unit",
        label="Unit",
        argv=["pytest"],
        log=".proof/unit.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )

    summary = proof.build_summary(
        [command],
        unit,
        e2e,
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    #: le contrat JSON historique est figé: verdict et chemins canoniques d'artefacts
    assert summary["ok"] is True
    assert summary["unit_log"] == ".proof/make-check-pytest.log"
    assert summary["e2e_log"] == ".proof/e2e-chrome.log"
    assert summary["report_html"] == ".proof/proof-report.html"


def test_build_summary_adds_project_evidence_sections():
    """Le summary embarque les sections projet (identité, matrice de
    validation, catalogue d'évidence, inconnues) qui alimentent le cockpit."""
    unit = {
        "path": ".proof/unit-junit.xml",
        "exists": True,
        "tests": 2,
        "passed": 2,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.1,
        "cases": [],
    }
    e2e = {
        "path": ".proof/e2e-junit.xml",
        "exists": True,
        "tests": 1,
        "passed": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.2,
        "cases": [],
    }
    command = proof.CommandEvidence(
        id="unit",
        label="Unit",
        argv=["pytest"],
        log=".proof/unit.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )
    git_context = {
        "branch": "feature",
        "sha": "abc123",
        "changed_files": [
            {"status": "M", "path": "Makefile"},
            {"status": "A", "path": "src/cdpx/proof.py"},
            {"status": "A", "path": "tests/test_proof.py"},
        ],
        "generated_files": [],
        "changed_count": 3,
        "generated_count": 0,
        "status_path": ".proof/git-status.txt",
        "diff_stat_path": ".proof/git-diff-stat.txt",
    }

    help_commands = proof.parse_help_commands(build_parser().format_help())

    summary = proof.build_summary(
        [command],
        unit,
        e2e,
        git_context=git_context,
        help_commands=help_commands,
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    #: l'identité et les volumes projet sont calculés depuis le dépôt, pas codés en dur
    assert summary["project"]["name"] == "cdpx"
    assert summary["project"]["cli_command_count"] >= 20
    assert summary["project"]["fixture_count"] >= 1
    #: matrice, catalogue et inconnues sont peuplés: la SPA a de quoi rendre chaque section
    assert summary["validation_matrix"]
    assert summary["coverage_groups"] == []
    assert any(item["type"] == "junit" for item in summary["evidence_catalog"])
    assert summary["unknowns"]


def test_build_summary_includes_symfony_suite_and_catalog():
    """Fournie, la suite Symfony entre dans le verdict, les totaux et le
    catalogue d'évidence au même titre que unit et e2e."""
    unit = {
        "path": ".proof/unit-junit.xml",
        "exists": True,
        "tests": 2,
        "passed": 2,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.1,
        "cases": [],
    }
    e2e = {
        "path": ".proof/e2e-junit.xml",
        "exists": True,
        "tests": 1,
        "passed": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.2,
        "cases": [],
    }
    symfony = {
        "path": ".proof/symfony-e2e-junit.xml",
        "exists": True,
        "tests": 1,
        "passed": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.3,
        "cases": [],
    }
    command = proof.CommandEvidence(
        id="symfony-e2e",
        label="Symfony E2E Docker",
        argv=["docker", "compose", "up"],
        log=".proof/symfony-e2e.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )

    summary = proof.build_summary(
        [command],
        unit,
        e2e,
        symfony,
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    #: la suite Symfony verte contribue aux totaux et publie log et JUnit au catalogue
    assert summary["ok"] is True
    assert summary["symfony_log"] == ".proof/symfony-e2e.log"
    assert summary["junit"]["symfony"]["tests"] == 1
    assert summary["totals"]["tests"] == 4
    assert any(item["name"] == "Symfony E2E JUnit" for item in summary["evidence_catalog"])


def test_write_symfony_unavailable_evidence_is_explicit(tmp_path, monkeypatch):
    """L'indisponibilité Symfony laisse une évidence explicite sur disque
    (suite, statut, raison) au lieu d'une absence silencieuse."""
    proof_dir = tmp_path / ".proof"
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setattr(proof, "EVIDENCE_DIR", proof_dir / "evidence")
    monkeypatch.setattr(proof, "SYMFONY_LOG", proof_dir / "symfony-e2e.log")
    proof.SYMFONY_LOG.parent.mkdir(parents=True)
    proof.SYMFONY_LOG.write_text("docker unavailable\n", encoding="utf-8")

    proof.write_symfony_unavailable_evidence("Docker daemon unavailable")

    payload = (proof.EVIDENCE_DIR / "symfony-scenarios.json").read_text(encoding="utf-8")
    #: le JSON écrit nomme la suite, le statut unavailable et la raison, lisibles par le cockpit
    assert '"suite": "symfony"' in payload
    assert '"status": "unavailable"' in payload
    assert "Docker daemon unavailable" in payload


def test_run_symfony_evidence_fails_when_docker_is_missing(tmp_path, monkeypatch):
    """Sans binaire docker, la collecte Symfony échoue franchement: statut
    unavailable, exit code non nul et log rappelant l'exigence release."""
    monkeypatch.setattr(proof, "EVIDENCE_DIR", tmp_path / "evidence")
    monkeypatch.setattr(proof, "SYMFONY_LOG", tmp_path / "symfony.log")
    monkeypatch.setattr(proof.shutil, "which", lambda _name: None)

    command = proof.run_symfony_evidence()

    #: l'absence de Docker est un échec tracé que le portail release peut juger, pas un skip
    assert command.exit_code == 1
    assert command.status == "unavailable"
    assert "required for release proof" in proof.SYMFONY_LOG.read_text(encoding="utf-8")


def _minimal_suite(path, tests=1, cases=None):
    cases = cases or []
    return {
        "path": path,
        "exists": True,
        "tests": tests,
        "passed": tests,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.1,
        "cases": cases,
    }


def _ok_command():
    return proof.CommandEvidence(
        id="unit",
        label="Unit",
        argv=["pytest"],
        log=".proof/unit.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )


def test_spa_renders_every_summary_key():
    """Garde-fou "calculé => rendu": toute clé de premier niveau du summary
    doit être consommée par la SPA, hors clés du shell HTML et métadonnées."""
    # Garde-fou "calculé => rendu": toute clé de premier niveau du summary doit
    # être lue par la SPA (data.<clé>), sauf celles rendues par le shell HTML ou
    # purement méta (chemins d'artefacts, duplicats bruts).
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        help_commands=proof.parse_help_commands(build_parser().format_help()),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )
    shell_keys = {"ok", "generated_at", "git"}  # rendus par render_html directement
    meta_keys = {"artifact_dir", "report_html", "unit_log", "e2e_log", "symfony_log"}
    meta_keys.add("scenario_evidence")  # duplicat brut de feature_inventory/matched_scenarios
    #: une clé calculée mais jamais lue par la SPA échoue ici: le travail mort est un bug
    for key in summary:
        if key in shell_keys | meta_keys or f"data.{key}" in proof.SPA_JS:
            continue
        raise AssertionError(f"clé du summary calculée mais jamais rendue par la SPA: {key}")


def test_render_html_embeds_payload_verdict_and_routes(evidence_case):
    """Le HTML rendu embarque le payload JSON et le verdict, câble toutes les
    routes de la SPA et verrouille le rapport par CSP, sans script externe."""
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )
    html = proof.render_html(summary)
    #: payload et verdict sont inlinés: le rapport est autonome et lisible hors ligne
    assert 'id="report-data"' in html and '"ok": true'.replace(" ", "") in html.replace(" ", "")
    assert ">OK<" in html
    #: chaque route de navigation attendue est présente dans le shell
    for route in (
        "#/features",
        "#/docs",
        "#/cli",
        "#/validation",
        "#/gaps",
        "#/run",
        "#/project",
    ):
        assert route in html
    #: CSP stricte et Mermaid en mode strict interdisent toute requête sortante
    assert "securityLevel: 'strict'" in html
    assert "connect-src 'none'" in html
    assert "media-src 'self'" in html
    #: la modale d'artefacts existe et s'annonce comme dialogue accessible
    assert 'id="artifact-modal"' in html
    assert 'role="dialog"' in html
    #: aucun script externe: l'autonomie hors ligne est structurelle
    assert "<script src=" not in html

    if evidence_case is not None:
        # Le HTML complet embarque Mermaid (~3,5 Mo): on n'attache qu'un extrait
        # significatif (tête du shell/CSP + zone du payload embarqué), sous le
        # cap d'inline de 16 KiB du cockpit.
        marker = html.index('id="report-data"')
        shell_excerpt = "\n".join(
            [
                "=== <head> / shell (extrait) ===",
                html[:1200],
                "=== zone du payload embarqué (report-data) ===",
                html[max(0, marker - 200) : marker + 3500],
            ]
        )
        evidence_case.attach_text(
            "Extrait du rapport HTML — shell + payload embarqué",
            shell_excerpt,
            filename="report-shell.html",
        )


def test_build_summary_exposes_curated_documentation_catalog():
    """Le catalogue documentaire du summary suit son schéma versionné, sans
    violation, et référence les documents curés attendus."""
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    documentation = summary["documentation"]
    #: schéma annoncé, zéro violation et présence d'un document clé: la curation est vérifiée
    assert documentation["schema"] == "cdpx.docs/v1"
    assert documentation["violations"] == []
    assert any(
        document["path"] == "docs/SESSION-LIFECYCLE.md" for document in documentation["documents"]
    )
    #: un catalogue sain ne déclenche aucune défaillance de portail
    assert not any(failure.startswith("documentation:") for failure in summary["proof_failures"])


def test_mermaid_vendor_bundle_is_integrity_checked_and_embedded(monkeypatch):
    """Le bundle Mermaid vendoré est chargé entier et vérifié par SHA-256:
    un hash divergent est rejeté plutôt que d'embarquer un script altéré."""
    bundle = proof._mermaid_bundle()
    #: le bundle complet est réellement lu depuis les ressources du paquet
    assert len(bundle) > 3_000_000
    assert "mermaid" in bundle.lower()

    proof._mermaid_bundle.cache_clear()
    monkeypatch.setattr(proof, "MERMAID_SHA256", "0" * 64)
    #: un hash inattendu fait échouer le chargement: intégrité avant embarquement
    with pytest.raises(ValueError, match="bundle vendor/mermaid"):
        proof._mermaid_bundle()
    proof._mermaid_bundle.cache_clear()


def test_xterm_vendor_bundle_is_integrity_checked_and_embedded(monkeypatch):
    """Le bundle xterm.js et sa feuille de style vendorés sont chargés et
    vérifiés par SHA-256, prêts à être inlinés comme Mermaid."""
    # Le player cast s'appuie sur xterm.js vendoré (MIT): bundle + CSS vérifiés
    # par SHA-256, embarqués inline dans le rapport comme Mermaid.
    bundle = proof._xterm_bundle()
    #: JS et CSS xterm sont présents et substantiels dans les ressources du paquet
    assert len(bundle) > 100_000
    assert "Terminal" in bundle

    stylesheet = proof._xterm_css()
    assert ".xterm" in stylesheet

    proof._xterm_bundle.cache_clear()
    monkeypatch.setattr(proof, "XTERM_JS_SHA256", "0" * 64)
    #: la vérification d'intégrité rejette un bundle dont le hash a changé
    with pytest.raises(ValueError, match="bundle vendor/xterm"):
        proof._xterm_bundle()
    proof._xterm_bundle.cache_clear()


def test_cockpit_assets_are_packaged_and_sane():
    """Chaque ressource cockpit packagée existe, n'est pas vide et reste
    inlinable; le shell se substitue sans placeholder manquant ni orphelin."""
    # La présentation vit dans des ressources dédiées (cockpit/) chargées via
    # importlib.resources: chaque asset doit exister, être non vide, et les
    # scripts/styles doivent rester inlinables (pas de </script> prématuré).
    from string import Template

    #: chaque asset embarqué est non vide et ne peut pas fermer prématurément la balise script
    for name in proof.COCKPIT_RESOURCES:
        asset = proof._cockpit_asset(name)
        assert asset.strip(), f"asset cockpit vide: {name}"
        if name != proof.COCKPIT_SHELL_RESOURCE:
            assert "</script" not in asset.lower(), f"asset non inlinable: {name}"

    shell = proof._cockpit_asset(proof.COCKPIT_SHELL_RESOURCE)
    # Le shell doit se substituer sans placeholder manquant ni $ littéral orphelin.
    rendered = Template(shell).substitute(
        verdict="OK",
        pill="ok",
        context="ctx",
        spa_css="",
        xterm_css="",
        payload="{}",
        mermaid_bundle="",
        xterm_bundle="",
        spa_js="",
    )
    #: la substitution complète du shell prouve qu'aucun placeholder n'est orphelin
    assert rendered.startswith("<!doctype html>")

    #: demander un asset inexistant échoue franchement au lieu de rendre une page vide
    with pytest.raises(FileNotFoundError):
        proof._cockpit_asset("cockpit/does-not-exist.js")


def test_every_artifact_type_has_a_dedicated_viewer():
    """Garde-fou taxonomie => rendu: chaque type d'artefact de la taxonomie
    fermée possède son entrée dans le registre VIEWERS du cockpit."""
    # Garde-fou "calculé => rendu" au niveau des artefacts: chaque type de la
    # taxonomie fermée doit avoir une entrée dans le registre VIEWERS du
    # cockpit. Un type collecté sans visualiseur casse le build.
    from cdpx.testing.evidence import ARTIFACT_TYPES

    #: le registre doit exister sous la forme attendue avant d'en inspecter le contenu
    assert "const VIEWERS = {" in proof.SPA_JS
    registry = proof.SPA_JS.split("const VIEWERS = {", 1)[1].split("};", 1)[0]
    #: un type collecté sans visualiseur casse le build: rien ne reste invisible au reviewer
    for artifact_type in sorted(ARTIFACT_TYPES):
        assert f"'{artifact_type}':" in registry, (
            f"type d'artefact sans visualiseur dans le cockpit: {artifact_type}"
        )


def test_text_viewers_are_specialized_per_type():
    """Chaque type textuel dispose d'un visualiseur spécialisé dans la SPA
    (console, réseau, JSON, profiler, logs, commande), pas d'un simple
    lien de téléchargement."""
    # Chaque type textuel a un vrai visualiseur, pas un simple lien: console
    # (niveaux + filtres), network (table statuts), json/profiler (arbre),
    # logs (lignes numérotées + surlignage), command (argv + exit code).
    #: chaque marqueur atteste qu'un visualiseur dédié est réellement câblé dans le JS
    for marker in (
        "function consoleViewer",
        "data-console-level",
        "function networkViewer",
        "net-status",
        "function jsonViewer",
        "JSON_NODE_BUDGET",
        "function profilerViewer",
        "function logViewer",
        "log-hit",
        "function commandViewer",
        "transcriptSection(body, 'stderr')",
    ):
        assert marker in proof.SPA_JS, f"visualiseur texte manquant: {marker}"


def test_modal_resolves_inline_content_by_path():
    """Les copies d'artefacts de feature_inventory (jamais inlinées côté
    Python) récupèrent dans le modal le contenu embarqué de la source unique
    scenario_evidence, résolu par path — plus de repli « Contenu non
    embarqué » quand le contenu existe dans le payload."""
    # feature_inventory duplique chaque artefact à plusieurs niveaux (proofs,
    # matched_scenarios): inliner ces copies côté Python multiplierait le
    # poids du rapport. La SPA résout donc l'inline par path au moment du
    # rendu, depuis la copie unique de scenario_evidence.suites.
    #: l'index est construit depuis scenario_evidence, la source unique inlinée
    for marker in (
        "const inlineByPath",
        "(data.scenario_evidence || {}).suites",
        "function resolveInline",
    ):
        assert marker in proof.SPA_JS, f"résolution inline par path manquante: {marker}"
    #: le modal enrichit l'artefact avant de choisir son visualiseur
    assert "resolveInline(modalState.items[modalState.index])" in proof.SPA_JS


def test_cast_viewer_replays_v2_in_xterm_and_keeps_a_raw_fallback():
    """Le player de casts rejoue l'asciicast v2 dans xterm.js avec une
    toolbar maison (scrubber, vitesses) et conserve une vue brute de repli."""
    # Player réel: xterm.js vendoré (MIT — asciinema-player est GPL-3), piloté
    # par la toolbar maison (scrubber, vitesses), vue brute de repli conservée.
    #: chaque brique du player (parsing v2, cycle de vie xterm, contrôles,
    #: repli brut) est présente
    for marker in (
        "function parseCast",
        "header.version !== 2",
        "globalThis.Terminal",
        "terminal.reset()",
        "terminal.dispose()",
        "function castViewer",
        "data-cast-scrub",
        "data-cast-rawtoggle",
        "'asciinema': castViewer",
        "requestAnimationFrame(tick)",
    ):
        assert marker in proof.SPA_JS, f"player asciinema incomplet: {marker}"


def test_cast_gate_blocks_the_verdict():
    """Le portail cast est bloquant: collecte absente ou statut dégradé
    rougissent le verdict avec une cause explicite dans proof_failures."""
    # Portail cast: sans entrées (ou avec un statut dégradé), le verdict passe
    # rouge et la cause est explicite dans proof_failures.
    missing = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
    )
    #: aucune collecte de cast => verdict rouge, un échec par démo attendue
    assert missing["ok"] is False
    assert any(failure.startswith("cast missing:") for failure in missing["proof_failures"])

    degraded_casts = generated_casts()
    degraded_casts[0]["status"] = "unavailable"
    degraded = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=degraded_casts,
    )
    #: un cast dégradé est bloquant et nomme la démo fautive
    assert degraded["ok"] is False
    assert any(failure.startswith("cast unavailable:") for failure in degraded["proof_failures"])
    #: le summary expose les entrées pour le rendu SPA (section Run)
    assert degraded["casts"] == degraded_casts


def test_catalog_casts_are_inlined_for_the_player(tmp_path, monkeypatch, evidence_case):
    """Les .cast du catalogue sont inlinés dans le payload HTML: sous la CSP
    du rapport, un simple lien serait injouable."""
    # Les .cast du catalogue (produits hors scénario pytest) doivent être
    # inlinés: sous la CSP du rapport, un lien seul serait injouable.
    monkeypatch.setattr(proof, "PROOF_DIR", tmp_path)
    cast_file = tmp_path / "cli-help.cast"
    cast_file.write_text('{"version": 2}\n[0.1, "o", "ok"]\n', encoding="utf-8")

    catalog = proof.build_evidence_catalog({"commands": []}, {}, {}, {})

    entry = next(item for item in catalog if item["type"] == "asciinema")
    #: le contenu voyage dans le payload HTML, prêt pour xterm
    assert entry["inline_content"].startswith('{"version": 2}')
    #: plus aucune entrée placeholder "optional": le cast est obligatoire
    assert not any(item.get("status") == "optional" for item in catalog)

    if evidence_case is not None:
        # Le .cast synthétique du catalogue, rejouable tel quel dans le player
        # xterm du cockpit (classé non uploadable par attach_cast).
        evidence_case.attach_cast(
            cast_file,
            "Cast synthétique du catalogue — rejouable dans le player xterm",
        )


def test_modal_and_keyboard_wiring_are_present():
    """La modale d'artefacts et sa navigation clavier (Échap, flèches,
    groupes) sont câblées dans le JS de la SPA."""
    #: ouverture, fermeture, raccourcis clavier et contexte de navigation sont tous câblés
    for marker in (
        "function openModal",
        "function closeModal",
        "'Escape'",
        "'ArrowRight'",
        "'ArrowLeft'",
        "data-modal-group",
        "ctx: {scenario, run}",
    ):
        assert marker in proof.SPA_JS, f"câblage modal manquant: {marker}"


def test_reading_order_timeline_and_badges_guide_the_review():
    """L'UX de relecture est guidée: les échecs remontent en tête, le
    parcours et la chronologie du run sont visuels, les badges annoncent les
    preuves avant le clic."""
    # UX de lecture: le rouge remonte ("À lire d'abord"), le parcours est
    # guidé, la chronologie du run est visuelle et les badges annoncent les
    # preuves avant le clic.
    #: chaque dispositif de guidage (à lire d'abord, parcours, timeline, badges) est câblé
    for marker in (
        "function renderReadFirst",
        "À lire d'abord",
        "function renderReadingPath",
        "function renderCommandTimeline",
        "tl-bad",
        "function decorateTopbar",
        "function failedRuns",
        "typeBadges(scenarioArtifacts(feature.matched_scenarios))",
    ):
        assert marker in proof.SPA_JS, f"UX de lecture incomplète: {marker}"


def test_scenario_view_renders_intent_and_assertion_hierarchy():
    """La vue scénario rend l'intention extraite du code: docstring,
    annotations d'assertion avec statut honnête, corrélation de la ligne
    d'échec et sorties du run."""
    # "Calculé => rendu" pour l'intention extraite du code: docstring,
    # assertions #: avec statut honnête, corrélation failed_line, chronologie.
    #: chaque champ d'intention calculé est consommé par la vue, y compris l'échec corrélé
    for marker in (
        "run.intent",
        "run.assertions",
        "run.failed_line",
        "assertion.status === 'failed'",
        "function renderTestCard",
        "function artifactTimeline",
        "function typeBadges",
        "run.stdout",
        "run.stderr",
    ):
        assert marker in proof.SPA_JS, f"vue scénario incomplète: {marker}"


def test_build_summary_embeds_cases_focus_and_log_tails(tmp_path):
    """Le summary embarque les cas JUnit détaillés, une liste focus qui fait
    remonter les échecs et la queue de log de chaque commande."""
    cases = [
        {"classname": "tests.test_a", "name": "test_x", "time_s": 0.5, "status": "passed"},
        {"classname": "tests.test_a", "name": "test_y", "time_s": 0.1, "status": "failed"},
    ]
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml", tests=2, cases=cases),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )
    #: cas complets restitués, échecs triés en tête du focus, queue de log prête au rendu
    assert summary["junit"]["unit"]["cases"] == cases
    assert summary["junit"]["unit"]["focus"][0]["status"] == "failed"  # échecs d'abord
    assert "log_tail" in summary["commands"][0]


def test_symfony_unavailable_is_always_blocking(monkeypatch):
    """Un scénario Symfony unavailable bloque le verdict même sans suite
    JUnit Symfony: l'indisponibilité est comptée et nommée."""
    suites = {
        "unit": [],
        "integration": [],
        "e2e": [],
        "symfony": [{"nodeid": "tests/e2e/test_e2e_symfony.py::test_x", "status": "unavailable"}],
    }
    evidence = {"suites": suites, "files": [], "totals": proof.scenario_totals(suites)}

    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=evidence,
    )
    #: l'indisponibilité apparaît dans les totaux, rougit le verdict et nomme sa cause
    assert summary["totals"]["unavailable"] == 1  # visible dans le hero
    assert summary["ok"] is False
    assert any("symfony evidence unavailable" in failure for failure in summary["proof_failures"])


def test_symfony_skips_are_release_blocking():
    """Un seul skip dans la suite Symfony fait échouer la preuve de release:
    aucun test esquivé n'est toléré sur ce portail."""
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        _minimal_suite(".proof/symfony-e2e-junit.xml", tests=2) | {"passed": 1, "skipped": 1},
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    #: le skip Symfony rougit le verdict et la défaillance le nomme explicitement
    assert summary["ok"] is False
    assert any("symfony tests skipped" in failure for failure in summary["proof_failures"])


def test_chrome_skips_and_missing_junit_are_release_blocking():
    """Côté Chrome e2e, un test skippé ou un JUnit absent sont tous deux
    bloquants pour la release, chacun avec une défaillance nommée."""
    e2e_command = proof.CommandEvidence(
        id="e2e",
        label="Chrome E2E",
        argv=["pytest"],
        log=".proof/e2e.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )
    skipped = _minimal_suite(".proof/e2e-junit.xml", tests=2) | {
        "passed": 1,
        "skipped": 1,
    }
    skipped_summary = proof.build_summary(
        [e2e_command],
        _minimal_suite(".proof/unit-junit.xml"),
        skipped,
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )
    missing_summary = proof.build_summary(
        [e2e_command],
        _minimal_suite(".proof/unit-junit.xml"),
        proof._empty_suite(proof.Path(".proof/e2e-junit.xml")),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    #: un seul skip e2e rougit le verdict, avec son compte exact dans la défaillance
    assert skipped_summary["ok"] is False
    assert "e2e tests skipped (1)" in skipped_summary["proof_failures"]
    #: l'absence du JUnit requis est une défaillance distincte, pas un zéro silencieux
    assert missing_summary["ok"] is False
    assert any("required JUnit missing" in item for item in missing_summary["proof_failures"])


def test_build_summary_fails_when_e2e_screenshot_missing():
    """Un scénario e2e sans capture attachée fait échouer la preuve et est
    aussi signalé comme non mappé dans l'inventaire des features."""
    unit = {
        "path": ".proof/unit-junit.xml",
        "exists": True,
        "tests": 0,
        "passed": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.0,
        "cases": [],
    }
    e2e = {
        "path": ".proof/e2e-junit.xml",
        "exists": True,
        "tests": 1,
        "passed": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.1,
        "cases": [],
    }
    command = proof.CommandEvidence(
        id="e2e",
        label="E2E",
        argv=["pytest"],
        log=".proof/e2e.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )
    suites = {
        "unit": [],
        "integration": [],
        "e2e": [
            {
                "nodeid": "tests/e2e/test_demo.py::test_without_shot",
                "status": "passed",
                "artifacts": [],
            }
        ],
    }
    scenario_evidence = {"suites": suites, "files": [], "totals": proof.scenario_totals(suites)}

    summary = proof.build_summary([command], unit, e2e, scenario_evidence=scenario_evidence)

    #: le scénario fautif est nommé dans la défaillance de capture manquante
    assert summary["ok"] is False
    assert (
        "missing e2e screenshot: tests/e2e/test_demo.py::test_without_shot"
        in summary["proof_failures"]
    )
    #: le même nodeid est aussi tracé comme non mappé dans l'inventaire des features
    assert (
        "feature inventory: scenario unmapped: tests/e2e/test_demo.py::test_without_shot"
        in summary["proof_failures"]
    )
