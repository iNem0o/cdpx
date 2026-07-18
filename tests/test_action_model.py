"""Contrat du langage d'actions composé, parsé une seule fois aux frontières."""

import pytest

from cdpx.action_model import (
    ClickAction,
    EvalAction,
    GotoAction,
    KeyAction,
    TypeAction,
    WaitAction,
    action_argv,
    parse_action,
)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["goto", "http://site.test/"], GotoAction("http://site.test/")),
        (["wait", "#ready"], WaitAction("#ready")),
        (["click", "#submit"], ClickAction("#submit")),
        (["type", "#name", "Ada"], TypeAction("#name", "Ada")),
        (["type", "#name", "Ada", "--clear"], TypeAction("#name", "Ada", clear=True)),
        (["key", "Enter"], KeyAction("Enter")),
        (
            ["eval", "document.title", "||", "'untitled'"],
            EvalAction("document.title || 'untitled'"),
        ),
    ],
)
def test_action_model_round_trips_cli_argv(argv, expected):
    parsed = parse_action(argv)

    assert parsed == expected
    assert parse_action(action_argv(parsed)) == expected


@pytest.mark.parametrize(
    "argv",
    [[], ["shell", "rm", "-rf"], ["click"], ["type", "#field", "value", "--unknown"]],
)
def test_action_model_rejects_ambiguous_or_unknown_argv(argv):
    with pytest.raises(ValueError, match="supported action|missing action"):
        parse_action(argv)
