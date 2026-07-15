"""Extraction statique d'intention (docstring + `#:`) — tests unitaires.

Les fonctions ``sample_*`` sont des témoins volontairement figés: leur source
EST la fixture. Ne pas les reformater sans adapter les assertions de lignes.
"""

import functools
import json

import pytest

from cdpx.security.redaction import RedactionContext
from cdpx.testing.evidence import EvidenceSession
from cdpx.testing.intent import (
    AssertionIntent,
    extract_intent,
    failure_location,
    mark_failed_assertion,
)


def sample_documented():
    """Vérifie que la valeur brute
    ne fuit jamais dans la sortie."""

    value = {"cookie": "***"}

    #: la valeur brute est remplacée par un masque
    assert value["cookie"] == "***"

    #: le dictionnaire ne contient
    #: aucune autre clé sensible
    assert list(value) == ["cookie"]


def sample_without_docstring():
    result = 1 + 1
    assert result == 2


def sample_inline_and_steps():
    payload = json.dumps({"ok": True})  #: la sérialisation reste déterministe

    #: préparer la charge utile décodée
    decoded = json.loads(payload)

    #: l'accès à une clé absente doit lever
    with pytest.raises(KeyError):
        decoded["absent"]

    #: note orpheline en fin de fonction


def _traced(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


@_traced
def sample_decorated():
    """Intention derrière décorateur."""

    #: l'assertion reste localisée malgré le wrapper
    assert True


async def sample_async():
    """Intention asynchrone."""

    #: l'extraction couvre les coroutines
    assert True


class FakeItem:
    def __init__(self, nodeid, function=None):
        self.nodeid = nodeid
        if function is not None:
            self.function = function

    def get_closest_marker(self, name):
        return None


class FakeCrash:
    def __init__(self, path, lineno):
        self.path = path
        self.lineno = lineno


class FakeLongrepr:
    def __init__(self, path, lineno):
        self.reprcrash = FakeCrash(path, lineno)


class FakeReport:
    duration = 0.01
    when = "call"
    capstdout = ""
    capstderr = ""

    def __init__(self, outcome="failed", longrepr=None, longreprtext=""):
        self.outcome = outcome
        self.longrepr = longrepr
        self.longreprtext = longreprtext


def test_extract_intent_reads_docstring_and_grouped_comments():
    intent = extract_intent(sample_documented)

    assert intent is not None
    assert intent.docstring.startswith("Vérifie que la valeur brute")
    #: la docstring est nettoyée (cleandoc), pas de retrait résiduel
    assert "\n    ne fuit" not in intent.docstring

    kinds = [assertion.kind for assertion in intent.assertions]
    assert kinds == ["assert", "assert"]
    first, second = intent.assertions
    assert first.text == "la valeur brute est remplacée par un masque"
    #: les commentaires `#:` consécutifs sont fusionnés en une seule intention
    assert second.text == "le dictionnaire ne contient aucune autre clé sensible"
    assert first.code_excerpt.startswith("assert value[")
    #: les lignes sont absolues dans le fichier de test
    assert first.line > 20


def test_extract_intent_handles_missing_docstring():
    intent = extract_intent(sample_without_docstring)

    assert intent is not None
    assert intent.docstring == ""
    assert intent.assertions == []


def test_extract_intent_covers_inline_steps_raises_and_orphans():
    intent = extract_intent(sample_inline_and_steps)

    assert intent is not None
    by_text = {assertion.text: assertion for assertion in intent.assertions}

    #: un commentaire en fin de ligne annote l'instruction qui le porte
    inline = by_text["la sérialisation reste déterministe"]
    assert inline.kind == "step"
    assert inline.code_excerpt.startswith("payload = json.dumps")

    #: une étape non-assert reçoit kind="step"
    step = by_text["préparer la charge utile décodée"]
    assert step.kind == "step"

    #: pytest.raises compte comme une assertion
    raises = next(a for a in intent.assertions if a.code_excerpt.startswith("with pytest.raises"))
    assert raises.kind == "assert"

    #: un commentaire sans instruction suivante devient une note visible
    orphan = by_text["note orpheline en fin de fonction"]
    assert orphan.kind == "note"
    assert orphan.code_excerpt == ""


def test_extract_intent_unwraps_decorators_and_supports_async():
    decorated = extract_intent(sample_decorated)
    assert decorated is not None
    assert decorated.docstring == "Intention derrière décorateur."
    assert decorated.assertions[0].kind == "assert"

    coroutine = extract_intent(sample_async)
    assert coroutine is not None
    assert coroutine.assertions[0].text == "l'extraction couvre les coroutines"


def test_extract_intent_fails_open_when_source_is_unavailable():
    #: une builtin n'a pas de source: aucune exception, juste None
    assert extract_intent(len) is None
    assert extract_intent(42) is None


def test_evidence_session_extracts_intent_once_for_parametrized_items(tmp_path):
    session = EvidenceSession(tmp_path, ttl=3600)
    first = session.case_for_item(
        FakeItem("tests/test_intent.py::sample_documented[a]", sample_documented)
    )
    second = session.case_for_item(
        FakeItem("tests/test_intent.py::sample_documented[b]", sample_documented)
    )

    assert first.intent.startswith("Vérifie")
    assert second.intent == first.intent
    assert len(session._intent_cache) == 1
    #: chaque case possède ses propres dicts d'assertions (corrélation isolée)
    assert first.assertions is not second.assertions
    assert first.assertions == second.assertions

    #: un item sans fonction (plugin exotique) ne casse pas la collecte
    bare = session.case_for_item(FakeItem("tests/test_intent.py::no_function"))
    assert bare.intent == ""


def test_failure_location_only_trusts_the_test_file():
    in_test = FakeReport(longrepr=FakeLongrepr("/repo/tests/test_demo.py", 42))
    assert failure_location(in_test, "tests/test_demo.py") == 42

    #: un échec dans un helper ne doit jamais incriminer une assertion du test
    in_helper = FakeReport(longrepr=FakeLongrepr("/repo/src/cdpx/testing/e2e.py", 99))
    assert failure_location(in_helper, "tests/test_demo.py") == 0

    #: repli sur le texte du longrepr quand reprcrash est absent
    fallback = FakeReport(
        longrepr=None,
        longreprtext="E assert 1 == 2\ntests/test_demo.py:57: AssertionError",
    )
    assert failure_location(fallback, "tests/test_demo.py") == 57

    assert failure_location(FakeReport(longrepr=None, longreprtext=""), "tests/x.py") == 0


def test_mark_failed_assertion_targets_the_covering_statement():
    assertions = [
        AssertionIntent(line=10, end_line=12, text="a", code_excerpt="assert a", kind="assert"),
        AssertionIntent(line=20, end_line=25, text="b", code_excerpt="with block", kind="step"),
        AssertionIntent(line=22, end_line=23, text="c", code_excerpt="assert c", kind="assert"),
        AssertionIntent(line=30, end_line=30, text="d", code_excerpt="", kind="note"),
    ]
    entries = [assertion.as_dict() for assertion in assertions]

    mark_failed_assertion(entries, 23)

    #: l'instruction la plus interne couvrant la ligne est incriminée, pas le bloc
    assert [entry["status"] for entry in entries] == ["", "", "failed", ""]

    #: une ligne hors de toute annotation ne marque rien
    fresh = [assertion.as_dict() for assertion in assertions]
    mark_failed_assertion(fresh, 5)
    assert all(entry["status"] == "" for entry in fresh)


def test_case_report_correlates_failed_line_with_assertions(tmp_path):
    session = EvidenceSession(tmp_path, ttl=3600)
    case = session.case_for_item(
        FakeItem("tests/test_intent.py::sample_documented", sample_documented)
    )
    failing = case.assertions[0]

    case.set_report(
        FakeReport(
            longrepr=FakeLongrepr("/repo/tests/test_intent.py", failing["line"]),
            longreprtext="E AssertionError",
        )
    )

    assert case.status == "failed"
    assert case.failed_line == failing["line"]
    assert case.assertions[0]["status"] == "failed"
    assert case.assertions[1]["status"] == ""


def test_intent_fields_are_redacted_in_case_payload(tmp_path):
    context = RedactionContext.from_secrets(["proof-canary-999"])

    def sample_secret():
        """Docstring avec proof-canary-999 dedans."""

        #: le commentaire cite proof-canary-999
        assert True

    session = EvidenceSession(tmp_path, ttl=3600, redaction_context=context)
    case = session.case_for_item(FakeItem("tests/test_intent.py::sample_secret", sample_secret))
    case.status = "passed"

    serialized = json.dumps(case.as_dict(), ensure_ascii=False)
    assert "proof-canary-999" not in serialized
    assert "***" in serialized
