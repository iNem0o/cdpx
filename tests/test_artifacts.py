from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cdpx.artifacts import (
    ArtifactClassification,
    ArtifactError,
    SecureArtifactWriter,
    purge_expired,
    scan_canaries,
)
from cdpx.security import RedactionContext


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_secure_writer_creates_private_atomic_manifest(tmp_path):
    """Tout artefact naît privé (0700/0600) et engagé dans un manifeste
    versionné avec empreinte sha256 — le socle d'intégrité sur lequel
    reposent toutes les vérifications de partage ultérieures."""
    writer = SecureArtifactWriter(tmp_path, "run-1")
    entry = writer.write_text(
        "logs/result.txt",
        "safe output",
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=True,
    )
    path = writer.run_dir / entry.path
    #: le contenu est intact et illisible pour tout autre utilisateur dès l'écriture
    assert path.read_text(encoding="utf-8") == "safe output"
    assert mode(writer.run_dir) == 0o700 and mode(path) == 0o600
    manifest = json.loads(writer.manifest_path.read_text(encoding="utf-8"))
    #: le manifeste, lui-même privé, scelle schéma, empreinte et taille de l'artefact
    assert mode(writer.manifest_path) == 0o600
    assert manifest["schema"] == "cdpx.artifacts/v1"
    assert manifest["artifacts"][0]["sha256"] == entry.sha256
    assert manifest["artifacts"][0]["bytes"] == len("safe output")


def test_opaque_and_secret_artifacts_can_never_be_shareable(tmp_path):
    """Le partage n'est pas négociable pour les classifications sensibles:
    demander upload_allowed=True sur SECRET ou OPAQUE_RESTRICTED est rejeté
    avant même que le contenu ne touche le disque."""
    writer = SecureArtifactWriter(tmp_path, "run-1")
    for classification in (
        ArtifactClassification.SECRET,
        ArtifactClassification.OPAQUE_RESTRICTED,
    ):
        #: la combinaison classification sensible + partage est une erreur, pas un avertissement
        with pytest.raises(ArtifactError, match="non partageable"):
            writer.write_text(
                f"{classification.value}.txt",
                "content",
                classification=classification,
                upload_allowed=True,
            )


def test_writer_refuses_traversal_absolute_paths_and_symlinks(tmp_path):
    """Aucune forme de chemin ne permet d'écrire ou de référencer hors du
    répertoire du run: traversée relative, chemin absolu et lien symbolique
    sont tous rejetés au nom du même confinement."""
    writer = SecureArtifactWriter(tmp_path, "run-1")
    for name in ("../escape.txt", "/tmp/escape.txt", "a/../../escape.txt"):
        #: chaque variante d'évasion (remontée, absolu, traversée imbriquée) est bloquée
        with pytest.raises(ArtifactError, match="chemin"):
            writer.write_text(name, "x")
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    link = writer.run_dir / "link.txt"
    link.symlink_to(target)
    #: un symlink déposé dans le run ne peut pas être adopté comme artefact légitime
    with pytest.raises(ArtifactError, match="symbolique"):
        writer.register_file(link, classification=ArtifactClassification.INTERNAL)


def test_writer_refuses_a_symbolic_artifact_root(tmp_path):
    """La racine des artefacts elle-même ne peut pas être un symlink: on ne
    peut pas rediriger silencieusement toute l'écriture du run ailleurs."""
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "root"
    root.symlink_to(target, target_is_directory=True)

    #: le refus intervient à la construction, avant la moindre écriture
    with pytest.raises(ArtifactError, match="symbolique"):
        SecureArtifactWriter(root, "run-1")


def test_writer_redacts_text_json_and_registered_text_files(tmp_path, evidence_case):
    """La redaction couvre les trois voies d'entrée (texte, JSON, fichier
    enregistré): la valeur secrète n'atteint jamais le disque du run, quelle
    que soit la façon dont l'artefact arrive."""
    secret = "artifact-canary-7359"
    writer = SecureArtifactWriter(
        tmp_path,
        "run-1",
        redaction_context=RedactionContext.from_secrets([secret]),
    )
    writer.write_text("message.log", f"Bearer abc.def {secret}")
    writer.write_json(
        "result.json",
        {"url": f"https://demo.test/?token={secret}", "token": secret},
    )
    source = tmp_path / "source.ndjson"
    source.write_text(f'{{"secret":"{secret}"}}\n', encoding="utf-8")
    writer.register_file(source, name="copy.ndjson")

    #: le scanner canari ne retrouve le secret nulle part, et chaque fichier
    #: porte le marqueur de redaction là où la valeur aurait dû apparaître
    assert scan_canaries(writer.run_dir, [secret]) == []
    message_redacted = (writer.run_dir / "message.log").read_text(encoding="utf-8")
    result_redacted = (writer.run_dir / "result.json").read_text(encoding="utf-8")
    assert "***" in message_redacted
    assert "***" in result_redacted
    assert "***" in (writer.run_dir / "copy.ndjson").read_text(encoding="utf-8")

    if evidence_case is not None:
        # On n'attache que la sortie DÉJÀ assainie par le writer, jamais la
        # valeur brute: la preuve visuelle montre le marqueur *** en place.
        message_proof = evidence_case.attach_text(
            "Journal redacté (message.log)", message_redacted, filename="message.log"
        )
        result_proof = evidence_case.attach_text(
            "Résultat redacté (result.json)", result_redacted, filename="result.json"
        )
        #: l'artefact de preuve produit ne contient jamais le canari, seulement
        #: la version déjà marquée par *** que voit le lecteur du cockpit
        assert secret not in Path(message_proof["path"]).read_text(encoding="utf-8")
        assert secret not in Path(result_proof["path"]).read_text(encoding="utf-8")


def test_shareable_staging_contains_only_manifested_allowed_files(tmp_path, evidence_case):
    """Le staging partageable fonctionne en liste blanche: seuls les fichiers
    manifestés ET autorisés à l'upload sont copiés, et le manifeste exporté
    ne trahit même pas l'existence du reste."""
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_json(
        "safe.json",
        {"ok": True},
        classification=ArtifactClassification.PUBLIC,
        upload_allowed=True,
    )
    writer.write_text(
        "private.log",
        "internal",
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=False,
    )
    staging = writer.build_shareable(tmp_path / "shareable")
    #: le fichier public autorisé est copié, le fichier interne non autorisé reste chez lui
    assert (staging / "safe.json").exists()
    assert not (staging / "private.log").exists()
    shared_manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    #: le manifeste partagé ne liste que ce qui a réellement été exporté
    assert [item["path"] for item in shared_manifest["artifacts"]] == ["safe.json"]

    if evidence_case is not None:
        evidence_case.attach_json(
            "Manifeste du staging partagé (liste blanche)",
            shared_manifest,
            filename="shared-manifest.json",
        )


def test_unmanifested_private_file_blocks_staging(tmp_path):
    """Un fichier apparu dans le run sans passer par le writer bloque le
    staging entier: rien d'inconnu ne peut se glisser dans un partage."""
    writer = SecureArtifactWriter(tmp_path, "run-1")
    writer.write_text("safe.txt", "safe")
    rogue = writer.run_dir / "rogue.txt"
    rogue.write_text("rogue", encoding="utf-8")
    #: le fichier orphelin est traité comme une compromission, pas simplement ignoré
    with pytest.raises(ArtifactError, match="non manifesté"):
        writer.build_shareable(tmp_path / "share")


def test_mutated_manifested_file_blocks_staging(tmp_path):
    """Un artefact modifié après écriture — donc après redaction — casse la
    vérification d'intégrité: le staging refuse de propager un contenu qui
    n'est plus celui qui a été assaini."""
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text(
        "safe.txt",
        "safe",
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=True,
    )
    (writer.run_dir / "safe.txt").write_text("secret-after-redaction", encoding="utf-8")

    #: le sha256 du manifeste sert de sceau: toute mutation post-redaction est fatale
    with pytest.raises(ArtifactError, match="intégrité"):
        writer.build_shareable(tmp_path / "share")

    #: l'échec est atomique — aucun répertoire de partage partiel n'est laissé derrière
    assert not (tmp_path / "share").exists()


def test_missing_manifested_file_blocks_staging(tmp_path):
    """La disparition d'un fichier manifesté est une anomalie bloquante:
    l'export ne se contente pas d'omettre silencieusement ce qui manque."""
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text("safe.txt", "safe", upload_allowed=True)
    (writer.run_dir / "safe.txt").unlink()

    #: un manifeste qui promet un fichier absent invalide le staging complet
    with pytest.raises(ArtifactError, match="introuvable"):
        writer.build_shareable(tmp_path / "share")


def test_replaced_manifested_file_symlink_blocks_staging(tmp_path):
    """Substituer un symlink à un artefact manifesté ne permet pas d'exfiltrer
    un fichier extérieur via la copie partageable."""
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text("safe.txt", "safe", upload_allowed=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-secret", encoding="utf-8")
    artifact = writer.run_dir / "safe.txt"
    artifact.unlink()
    artifact.symlink_to(outside)

    #: le lien substitué est démasqué au moment de la copie, malgré un nom manifesté
    with pytest.raises(ArtifactError, match="symbolique"):
        writer.build_shareable(tmp_path / "share")

    #: rien n'a été copié: le contenu extérieur n'a jamais quitté sa place
    assert not (tmp_path / "share").exists()


def test_overly_permissive_manifested_file_blocks_staging(tmp_path):
    """Les permissions privées font partie du contrat vérifié: un artefact
    devenu lisible par d'autres n'est plus digne d'être partagé."""
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text("safe.txt", "safe", upload_allowed=True)
    (writer.run_dir / "safe.txt").chmod(0o644)

    #: l'élargissement des droits est détecté avant toute copie hors du run
    with pytest.raises(ArtifactError, match="permissions"):
        writer.build_shareable(tmp_path / "share")


def test_canary_scanner_and_expiration_purge(tmp_path):
    """Le scanner canari retrouve la valeur secrète jusque dans un artefact
    binaire opaque, et la purge TTL efface réellement les runs expirés."""
    writer = SecureArtifactWriter(tmp_path, "expired", ttl=1)
    writer.write_bytes(
        "leak.bin",
        b"CANARY-SECRET",
        classification=ArtifactClassification.OPAQUE_RESTRICTED,
    )
    #: le canari planté est localisé même dans un blob binaire non textuel
    assert scan_canaries(writer.run_dir, ["CANARY-SECRET"]) == ["leak.bin"]
    future = datetime.now(UTC) + timedelta(seconds=2)
    #: passé le TTL, le run est purgé du disque et son identifiant rapporté
    assert purge_expired(tmp_path, now=future) == ["expired"]
    assert not writer.run_dir.exists()
