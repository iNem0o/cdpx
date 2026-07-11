"""Interpréteur d'actions composées.

Une "action" est un argv compact (["click", "#sel"]) exécuté dans une
connexion CDP déjà ouverte. C'est le langage commun des commandes composées
(dom-diff, record, replay, emulate): une action = une primitive nommée,
jamais d'échappatoire shell. Le garde d'origine (advanced.command_mutates)
s'appuie sur le verbe de l'action pour classer lecture vs mutation.
"""

from __future__ import annotations

from cdpx.client import CDPClient
from cdpx.primitives import inputs, js, nav

USAGE = (
    "action supportée: goto <url>, wait <selector>, click <selector>, "
    "type <selector> <texte> [--clear], key <touche>, eval <js>"
)

MUTATING_VERBS = {"click", "type", "key", "eval"}


def validate_action(action: list[str]) -> None:
    """Valide intégralement un argv d'action sans toucher au navigateur."""
    if not action:
        raise ValueError(f"action manquante ({USAGE})")
    name, rest = action[0], action[1:]
    valid = (
        (name in {"goto", "wait", "click", "key"} and len(rest) == 1)
        or (name == "eval" and bool(rest))
        or (name == "type" and len(rest) in {2, 3} and (len(rest) == 2 or rest[2] == "--clear"))
    )
    if not valid or not all(isinstance(item, str) for item in action):
        raise ValueError(USAGE)


def run_action(client: CDPClient, action: list[str], timeout: float = 30.0) -> dict:
    """Exécute une action et retourne la sortie de la primitive sous-jacente."""
    validate_action(action)
    name, rest = action[0], action[1:]
    if name == "goto" and len(rest) == 1:
        result = nav.navigate(client, rest[0], timeout=timeout)
        if result.get("ok") is False:
            raise ValueError(f"navigation échouée: {result.get('errorText') or rest[0]}")
        return result
    if name == "wait" and len(rest) == 1:
        return nav.wait_for(client, rest[0], timeout=min(timeout, 10.0))
    if name == "click" and len(rest) == 1:
        return inputs.click(client, rest[0])
    if name == "type" and len(rest) >= 2:
        clear = "--clear" in rest[2:]
        return inputs.type_text(client, rest[0], rest[1], clear=clear)
    if name == "key" and len(rest) == 1:
        return inputs.press_key(client, rest[0])
    if name == "eval" and rest:
        return {"value": js.evaluate(client, " ".join(rest), await_promise=True)}
    raise ValueError(USAGE)
