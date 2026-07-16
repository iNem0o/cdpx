"""Modèles typés des fichiers ``*-scenarios.json`` (schéma ``cdpx.scenarios/v2``).

Ces TypedDict décrivent le contrat de données partagé entre le plugin
d'évidence pytest (écrivain), ``cdpx.proof`` (lecteur/réécrivain) et le
cockpit HTML (consommateur du payload embarqué). La plupart des champs sont
optionnels (``total=False``): les payloads legacy v1 et les scénarios
synthétiques (ex. Symfony ``unavailable``) n'en portent qu'un sous-ensemble.
Seul ``nodeid`` est requis — c'est la clé de corrélation de tout le pipeline.
"""

from __future__ import annotations

from typing import Any, Required, TypedDict, cast

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

    nodeid: Required[str]
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


_SCENARIO_STRINGS = (
    "suite",
    "title",
    "area",
    "feature",
    "journey",
    "scenario_id",
    "intent",
    "started_at",
    "status",
    "phase",
    "message",
    "stdout",
    "stderr",
    "scenario",
    "ui_text",
    "report_text",
    "given",
    "when",
    "then",
)
_SCENARIO_INTS = ("intent_line", "failed_line")
_SCENARIO_STRING_LISTS = ("proves", "expected_proofs")
_ASSERTION_STRINGS = ("text", "code_excerpt", "kind", "status")
_ASSERTION_INTS = ("line", "end_line")
_ARTIFACT_STRINGS = (
    "type",
    "label",
    "path",
    "mime",
    "created_at",
    "excerpt",
    "inline_content",
    "inline_skipped",
)


def validated_scenario_file(payload: Any, *, source: str, expected_schema: str) -> ScenarioFile:
    """Validate raw JSON and construct a fresh strict ``ScenarioFile``."""

    if not isinstance(payload, dict):
        raise ArtifactError(
            f"{source}: racine attendue comme objet JSON, reçu {type(payload).__name__}"
        )
    schema = payload.get("schema")
    if schema is not None and schema != expected_schema:
        raise ArtifactError(
            f"{source}: schéma de scénarios inattendu: attendu={expected_schema}, reçu={schema}"
        )
    raw_scenarios = payload.get("scenarios", [])
    if not isinstance(raw_scenarios, list):
        raise ArtifactError(
            f"{source}: `scenarios` doit être une liste, reçu {type(raw_scenarios).__name__}"
        )
    normalized: dict[str, Any] = {
        "scenarios": [
            _validated_scenario(value, source=source, index=index)
            for index, value in enumerate(raw_scenarios)
        ]
    }
    if schema is not None:
        normalized["schema"] = schema
    _copy_optional_scalars(payload, normalized, ("suite", "generated_at"), str, source)
    _copy_optional_scalars(payload, normalized, ("count",), int, source)
    return cast(ScenarioFile, normalized)


def _validated_scenario(value: Any, *, source: str, index: int) -> Scenario:
    where = f"{source}: scenarios[{index}]"
    if not isinstance(value, dict):
        raise ArtifactError(f"{where} doit être un objet JSON")
    nodeid = value.get("nodeid")
    if not isinstance(nodeid, str) or not nodeid:
        raise ArtifactError(f"{where} sans `nodeid` textuel non vide")
    normalized: dict[str, Any] = {"nodeid": nodeid}
    _copy_optional_scalars(value, normalized, _SCENARIO_STRINGS, str, where)
    _copy_optional_scalars(value, normalized, _SCENARIO_INTS, int, where)
    if "duration_s" in value:
        duration = value["duration_s"]
        if isinstance(duration, bool) or not isinstance(duration, int | float):
            raise ArtifactError(f"{where}: `duration_s` doit être numérique")
        normalized["duration_s"] = float(duration)
    for field in _SCENARIO_STRING_LISTS:
        if field in value:
            normalized[field] = _string_list(value[field], f"{where}: `{field}`")
    if "assertions" in value:
        normalized["assertions"] = _validated_assertions(value["assertions"], where)
    if "artifacts" in value:
        normalized["artifacts"] = _validated_artifacts(value["artifacts"], where)
    return cast(Scenario, normalized)


def _validated_assertions(value: Any, where: str) -> list[ScenarioAssertion]:
    if not isinstance(value, list):
        raise ArtifactError(f"{where}: `assertions` doit être une liste")
    assertions: list[ScenarioAssertion] = []
    for index, raw in enumerate(value):
        item_where = f"{where}: assertions[{index}]"
        if not isinstance(raw, dict):
            raise ArtifactError(f"{item_where} doit être un objet")
        normalized: dict[str, Any] = {}
        _copy_optional_scalars(raw, normalized, _ASSERTION_STRINGS, str, item_where)
        _copy_optional_scalars(raw, normalized, _ASSERTION_INTS, int, item_where)
        assertions.append(cast(ScenarioAssertion, normalized))
    return assertions


def _validated_artifacts(value: Any, where: str) -> list[ScenarioArtifact]:
    if not isinstance(value, list):
        raise ArtifactError(f"{where}: `artifacts` doit être une liste")
    artifacts: list[ScenarioArtifact] = []
    for index, raw in enumerate(value):
        item_where = f"{where}: artifacts[{index}]"
        if not isinstance(raw, dict):
            raise ArtifactError(f"{item_where} doit être un objet")
        normalized: dict[str, Any] = {}
        _copy_optional_scalars(raw, normalized, _ARTIFACT_STRINGS, str, item_where)
        _copy_optional_scalars(raw, normalized, ("bytes",), int, item_where)
        _copy_optional_scalars(raw, normalized, ("truncated",), bool, item_where)
        artifacts.append(cast(ScenarioArtifact, normalized))
    return artifacts


def _copy_optional_scalars(
    source: dict[str, Any],
    target: dict[str, Any],
    fields: tuple[str, ...],
    expected_type: type,
    where: str,
) -> None:
    for field in fields:
        if field not in source:
            continue
        value = source[field]
        if expected_type is int and isinstance(value, bool):
            valid = False
        else:
            valid = isinstance(value, expected_type)
        if not valid:
            raise ArtifactError(f"{where}: `{field}` doit être de type {expected_type.__name__}")
        target[field] = value


def _string_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ArtifactError(f"{where} doit être une liste de textes")
    return list(value)
