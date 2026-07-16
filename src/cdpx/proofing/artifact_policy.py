"""Politique de classification et de purge des artefacts de preuve.

Seuls les fichiers produits par le pipeline lui-même peuvent être classés par
la politique MIME; tout le reste doit être couvert par un manifeste
d'évidence, sinon le staging échoue fermé. Aucun symbole de ce module ne lit
`cdpx.proof` à l'exécution: la façade ré-exporte ces primitives.
"""

from __future__ import annotations

import mimetypes
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from cdpx.artifacts import ArtifactClassification, ArtifactError
from cdpx.proofing.evidence_policy import EVIDENCE_SCHEMA
from cdpx.proofing.execution import _read_json_or_fail, _rewrite_text_paths
from cdpx.proofing.private_io import _write_private_text
from cdpx.security.redaction import RedactionContext, redact_text

_TEXTUAL_PROOF_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".log",
    ".md",
    ".txt",
    ".xml",
    ".yml",
    ".yaml",
}

# Allowlist explicite et bornée des fichiers produits par le pipeline de
# preuve lui-même (hors sessions pytest): eux seuls peuvent être classés par
# la politique MIME. Tout autre fichier doit être couvert par un manifeste
# d'évidence, sinon le staging échoue fermé. Les noms reflètent les constantes
# de chemin de la façade `cdpx.proof` (REPORT_HTML, SUMMARY_JSON, …), figées
# à l'import comme le contrat historique.
_PIPELINE_TOP_LEVEL_FILES = frozenset(
    {
        "proof-report.html",
        "validation-summary.json",
        "make-check-pytest.log",
        "e2e-chrome.log",
        "symfony-e2e.log",
        "cdpx-help.txt",
        "git-status.txt",
        "git-diff-stat.txt",
        "symfony-e2e-junit.xml",
        "unit-junit.xml",
        "e2e-junit.xml",
        "ruff-check.log",
        "ruff-format.log",
        "mypy.log",
        "artifact-manifest.json",
    }
)
# Ordre de restriction croissant pour la fusion multi-manifestes.
_CLASSIFICATION_SEVERITY: dict[ArtifactClassification, int] = {
    ArtifactClassification.PUBLIC: 0,
    ArtifactClassification.INTERNAL: 1,
    ArtifactClassification.OPAQUE_RESTRICTED: 2,
    ArtifactClassification.SECRET: 3,
}


def _sanitize_text_file(
    path: Path,
    context: RedactionContext,
    path_rewrites: Sequence[tuple[str, str]] = (),
) -> None:
    if not path.exists() or path.is_symlink():
        return
    value = path.read_text(encoding="utf-8", errors="replace")
    cleaned = redact_text(
        _rewrite_text_paths(value, path_rewrites), context=context, path=f"$.files.{path.name}"
    )
    _write_private_text(path, cleaned)


def _proof_artifact_policy(path: Path) -> tuple[ArtifactClassification, bool]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime.startswith("text/") or path.suffix.lower() in _TEXTUAL_PROOF_SUFFIXES:
        return ArtifactClassification.INTERNAL, True
    return ArtifactClassification.OPAQUE_RESTRICTED, False


def _is_pipeline_proof_artifact(relative: str) -> bool:
    parts = Path(relative).parts
    if len(parts) == 1:
        return parts[0] in _PIPELINE_TOP_LEVEL_FILES or parts[0].endswith(".cast")
    if len(parts) == 2 and parts[0] == "evidence":
        # Les *-scenarios.json sont réécrits par _generate() après les runs
        # (symfony-scenarios.json peut même exister sans manifeste); les
        # manifestes eux-mêmes sont des métadonnées produites par les sessions.
        name = parts[1]
        return name.endswith("-scenarios.json") or (
            name.startswith("evidence-manifest") and name.endswith(".json")
        )
    return False


def _load_evidence_policy(proof_dir: Path) -> dict[Path, tuple[ArtifactClassification, bool]]:
    """Agrège les manifestes d'évidence en une politique par chemin résolu.

    Les manifestes écrits par les sessions pytest sont la seule autorité de
    classification des artefacts d'évidence: en cas de doublon entre
    manifestes, la classification la plus restrictive gagne et l'upload n'est
    permis que si tous l'autorisent.
    """

    evidence_root = (proof_dir / "evidence").resolve()
    policy: dict[Path, tuple[ArtifactClassification, bool]] = {}
    redaction_policies: set[str] = set()
    for manifest_path in sorted((proof_dir / "evidence").glob("evidence-manifest*.json")):
        payload = _read_json_or_fail(manifest_path, "manifeste d'évidence illisible")
        if not isinstance(payload, dict) or payload.get("schema") != EVIDENCE_SCHEMA:
            raise ArtifactError(f"schéma de manifeste d'évidence inattendu: {manifest_path}")
        redaction_policies.add(str(payload.get("redaction_policy")))
        for entry in payload.get("artifacts", []):
            try:
                resolved = (evidence_root / str(entry["path"])).resolve()
                classification = ArtifactClassification(str(entry["classification"]))
                upload_allowed = bool(entry["upload_allowed"])
            except (KeyError, TypeError, ValueError) as e:
                raise ArtifactError(
                    f"entrée de manifeste d'évidence invalide dans {manifest_path}: {e}"
                ) from e
            if resolved != evidence_root and evidence_root not in resolved.parents:
                raise ArtifactError(f"chemin manifesté hors de l'évidence: {entry['path']}")
            previous = policy.get(resolved)
            if previous is not None:
                if _CLASSIFICATION_SEVERITY[previous[0]] > _CLASSIFICATION_SEVERITY[classification]:
                    classification = previous[0]
                upload_allowed = upload_allowed and previous[1]
            policy[resolved] = (classification, upload_allowed)
    if len(redaction_policies) > 1:
        raise ArtifactError(
            "politiques de redaction hétérogènes entre manifestes d'évidence: "
            + ", ".join(sorted(redaction_policies))
        )
    return policy


def _docker_chown_remedy(root: Path) -> str:
    """Remède standard aux fichiers root laissés par un run Docker interrompu."""

    return (
        f'réparer avec `docker run --rm -v "$PWD/{root.name}:/t" alpine '
        'chown -R "$(id -u):$(id -g)" /t` puis relancer'
    )


def _raise_actionable_permission_error(root: Path, exc: PermissionError) -> NoReturn:
    """Convertit une PermissionError du staging en erreur actionnable.

    Un conteneur Symfony tué avant son chown final laisse des fichiers root
    dans l'arbre: plutôt qu'une PermissionError brute au milieu du run, on
    nomme le répertoire fautif et le remède.
    """

    raise ArtifactError(
        f"staging résiduel non purgeable: {root} (fichiers appartenant "
        f"probablement à root après un run Docker interrompu); {_docker_chown_remedy(root)}"
    ) from exc


def _purge_unmanifested_evidence(proof_dir: Path) -> list[str]:
    """Purge les artefacts d'évidence orphelins d'un pytest mort sans épilogue.

    Un pytest interrompu (deadline exit 124, SIGKILL, OOM 137, segfault)
    n'exécute pas ``pytest_sessionfinish``: ses artefacts attach_* déjà écrits
    n'ont aucun manifeste, et le staging partageable échouerait fermé avec un
    message trompeur. On retire ces orphelins de l'arbre — la suite tuée est
    déjà un échec de commande visible au verdict — plutôt que de masquer la
    cause réelle.
    """

    artifacts_root = proof_dir / "evidence" / "artifacts"
    if not artifacts_root.is_dir():
        return []
    policy = _load_evidence_policy(proof_dir)
    removed: list[str] = []
    for path in sorted(artifacts_root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ArtifactError(f"lien symbolique interdit dans les preuves: {path}")
        if path.is_file() and path.resolve() not in policy:
            path.unlink()
            removed.append(path.relative_to(proof_dir).as_posix())
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return removed
