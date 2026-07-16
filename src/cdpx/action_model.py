"""Domain model for the small, shell-free browser action language."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

USAGE = (
    "action supportée: goto <url>, wait <selector>, click <selector>, "
    "type <selector> <texte> [--clear], key <touche>, eval <js>"
)


@dataclass(frozen=True)
class GotoAction:
    url: str
    verb: str = "goto"


@dataclass(frozen=True)
class WaitAction:
    selector: str
    verb: str = "wait"


@dataclass(frozen=True)
class ClickAction:
    selector: str
    verb: str = "click"


@dataclass(frozen=True)
class TypeAction:
    selector: str
    text: str
    clear: bool = False
    verb: str = "type"


@dataclass(frozen=True)
class KeyAction:
    key: str
    verb: str = "key"


@dataclass(frozen=True)
class EvalAction:
    expression: str
    verb: str = "eval"


BrowserAction: TypeAlias = (
    GotoAction | WaitAction | ClickAction | TypeAction | KeyAction | EvalAction
)


def parse_action(argv: list[str]) -> BrowserAction:
    """Parse and validate the positional CLI/journal representation once."""
    if not argv or not all(isinstance(item, str) for item in argv):
        raise ValueError(f"action manquante ({USAGE})" if not argv else USAGE)
    verb, arguments = argv[0], argv[1:]
    if verb == "goto" and len(arguments) == 1:
        return GotoAction(arguments[0])
    if verb == "wait" and len(arguments) == 1:
        return WaitAction(arguments[0])
    if verb == "click" and len(arguments) == 1:
        return ClickAction(arguments[0])
    if verb == "type" and len(arguments) in {2, 3}:
        if len(arguments) == 3 and arguments[2] != "--clear":
            raise ValueError(USAGE)
        return TypeAction(arguments[0], arguments[1], clear=len(arguments) == 3)
    if verb == "key" and len(arguments) == 1:
        return KeyAction(arguments[0])
    if verb == "eval" and arguments:
        return EvalAction(" ".join(arguments))
    raise ValueError(USAGE)


def action_argv(action: BrowserAction) -> list[str]:
    """Render the stable CLI representation at an external boundary."""
    if isinstance(action, GotoAction):
        return [action.verb, action.url]
    if isinstance(action, WaitAction | ClickAction):
        return [action.verb, action.selector]
    if isinstance(action, TypeAction):
        return [action.verb, action.selector, action.text] + (["--clear"] if action.clear else [])
    if isinstance(action, KeyAction):
        return [action.verb, action.key]
    return [action.verb, action.expression]
