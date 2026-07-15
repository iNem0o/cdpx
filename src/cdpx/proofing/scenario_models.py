"""Modèles typés des fichiers ``*-scenarios.json`` (schéma ``cdpx.scenarios/v2``).

Ces TypedDict décrivent le contrat de données partagé entre le plugin
d'évidence pytest (écrivain), ``cdpx.proof`` (lecteur/réécrivain) et le
cockpit HTML (consommateur du payload embarqué). La plupart des champs sont
optionnels (``total=False``): les payloads legacy v1 et les scénarios
synthétiques (ex. Symfony ``unavailable``) n'en portent qu'un sous-ensemble.
Seul ``nodeid`` est requis — c'est la clé de corrélation de tout le pipeline.
"""

from __future__ import annotations

from typing import Any, TypedDict, cast

from cdpx.artifacts import ArtifactError


class ScenarioAssertion(TypedDict, total=False):
    """Assertion annotée ``#:`` extraite du code du test."""

    line: int
    end_line: int
    text: str
    code_excerpt: str
    kind: str
    status: str


class ScenarioArtifact(TypedDict, total=False):
    """Artefact attaché à un scénario (capture, log, json, cast, …)."""

    type: str
    label: str
    path: str
    bytes: int
    mime: str
    created_at: str
    excerpt: str
    # Champs d'inline ajoutés au rendu (inline_scenario_artifacts): le contenu
    # voyage dans le payload HTML, jamais dans les JSON réécrits sur disque.
    inline_content: str
    inline_skipped: str
    truncated: bool


class Scenario(TypedDict, total=False):
    """Scénario documenté d'un run pytest (ou synthétique, ex. Symfony)."""

    nodeid: str
    suite: str
    title: str
    area: str
    feature: str
    journey: str
    scenario_id: str
    proves: list[str]
    intent: str
    intent_line: int
    assertions: list[ScenarioAssertion]
    failed_line: int
    started_at: str
    duration_s: float
    status: str
    phase: str
    message: str
    stdout: str
    stderr: str
    artifacts: list[ScenarioArtifact]
    scenario: str
    ui_text: str
    report_text: str
    given: str
    when: str
    then: str
    expected_proofs: list[str]


class ScenarioFile(TypedDict, total=False):
    """Racine versionnée d'un fichier ``*-scenarios.json``.

    ``schema`` est absent des payloads legacy v1, tolérés tels quels par les
    lecteurs; toute autre valeur que ``cdpx.scenarios/v2`` est une erreur.
    """

    schema: str
    suite: str
    generated_at: str
    count: int
    scenarios: list[Scenario]


class ScenarioTotals(TypedDict):
    """Totaux agrégés par ``scenario_totals`` pour le hero du cockpit."""

    scenarios: int
    unit: int
    integration: int
    e2e: int
    symfony: int
    screenshots: int
    missing_e2e_screenshots: list[str]


class ScenarioEvidence(TypedDict):
    """Évidence agrégée retournée par ``load_scenario_evidence``."""

    suites: dict[str, list[Scenario]]
    files: list[str]
    totals: ScenarioTotals


def validated_scenario_file(payload: Any, *, source: str, expected_schema: str) -> ScenarioFile:
    """Valide structurellement un payload ``*-scenarios.json`` déjà décodé.

    La validation reste volontairement minimale (racine objet, schéma connu ou
    absent — tolérance legacy v1 —, ``scenarios`` liste de scénarios portant un
    ``nodeid`` textuel, ``status`` textuel et ``artifacts`` liste si présents):
    elle localise dans ``source`` les erreurs qui, sinon, exploseraient en
    KeyError/TypeError anonymes bien plus loin dans la génération de preuve.
    """

    if not isinstance(payload, dict):
        raise ArtifactError(
            f"{source}: racine attendue comme objet JSON, reçu {type(payload).__name__}"
        )
    schema = payload.get("schema")
    if schema is not None and schema != expected_schema:
        raise ArtifactError(
            f"{source}: schéma de scénarios inattendu: attendu={expected_schema}, reçu={schema}"
        )
    scenarios = payload.get("scenarios", [])
    if not isinstance(scenarios, list):
        raise ArtifactError(
            f"{source}: `scenarios` doit être une liste, reçu {type(scenarios).__name__}"
        )
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise ArtifactError(f"{source}: scenarios[{index}] doit être un objet JSON")
        nodeid = scenario.get("nodeid")
        if not isinstance(nodeid, str) or not nodeid:
            raise ArtifactError(f"{source}: scenarios[{index}] sans `nodeid` textuel non vide")
        status = scenario.get("status")
        if status is not None and not isinstance(status, str):
            raise ArtifactError(
                f"{source}: scenarios[{index}] ({nodeid}): `status` doit être textuel"
            )
        artifacts = scenario.get("artifacts")
        if artifacts is not None and not isinstance(artifacts, list):
            raise ArtifactError(
                f"{source}: scenarios[{index}] ({nodeid}): `artifacts` doit être une liste"
            )
    # L'essentiel est validé ci-dessus; le reste des champs voyage tel quel
    # (les lecteurs restent tolérants aux payloads legacy v1 et aux champs
    # additionnels des suites runtime).
    return cast(ScenarioFile, payload)
