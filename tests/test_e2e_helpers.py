"""Helpers e2e testables sans Chrome: bandeau de preuve éphémère."""

from pathlib import Path

import pytest

from cdpx.testing import e2e
from cdpx.testing.evidence import EvidenceCase


def _case(tmp_path):
    return EvidenceCase(
        nodeid="tests/e2e/test_demo.py::test_banner",
        root=tmp_path,
        suite="e2e",
        title="banner",
    )


def test_banner_scripts_are_json_escaped_and_self_cleaning():
    """Le wording du bandeau de preuve est neutralisé (échappement JSON +
    textContent) et le script jumeau de nettoyage cible le même nœud: le
    bandeau ne peut ni injecter de HTML ni survivre à la capture."""
    script = e2e.banner_inject_script('Formulaire soumis — état "final"')

    #: le wording est passé en JSON et affecté via textContent: pas d'injection
    assert '\\"final\\"' in script
    assert "textContent" in script and "innerHTML" not in script
    assert "cdpx-proof-banner" in script
    #: le bandeau est fixed bottom: il n'altère jamais le layout mesuré
    assert "position:fixed" in script

    cleanup = e2e.banner_cleanup_script()
    #: le script de nettoyage retire exactement le nœud que l'injection a créé
    assert "remove()" in cleanup and "cdpx-proof-banner" in cleanup


def test_attach_screenshot_injects_then_always_removes_the_banner(tmp_path, monkeypatch):
    """La capture avec bandeau suit l'ordre strict injection → capture →
    nettoyage: le screenshot montre le bandeau mais la page n'en garde
    aucune trace après coup."""
    calls = []

    def fake_evaluate(client, expression, **kwargs):
        calls.append("inject" if "appendChild" in expression else "cleanup")
        return True

    def fake_screenshot(client, path, *, full_page=False):
        calls.append("capture")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as stream:
            stream.write(b"\x89PNG\r\n\x1a\n")
        return {"path": path, "bytes": 8}

    monkeypatch.setattr(e2e.js, "evaluate", fake_evaluate)
    monkeypatch.setattr(e2e.capture, "screenshot", fake_screenshot)

    artifact = e2e.attach_screenshot(_case(tmp_path), object(), "final", banner="Étape 3")

    #: injection avant capture, suppression après: la page redevient intacte
    assert calls == ["inject", "capture", "cleanup"]
    assert artifact["type"] == "screenshot"


def test_attach_screenshot_removes_the_banner_even_when_capture_fails(tmp_path, monkeypatch):
    """Un échec de capture ne laisse jamais la page polluée: l'erreur remonte
    à l'appelant mais le bandeau est retiré quand même."""
    calls = []

    monkeypatch.setattr(e2e.js, "evaluate", lambda client, expression, **kwargs: calls.append("js"))

    def broken_screenshot(client, path, *, full_page=False):
        raise RuntimeError("capture down")

    monkeypatch.setattr(e2e.capture, "screenshot", broken_screenshot)

    #: l'échec de capture n'est pas avalé: l'appelant sait que la preuve manque
    with pytest.raises(RuntimeError, match="capture down"):
        e2e.attach_screenshot(_case(tmp_path), object(), "final", banner="Étape 3")

    #: le finally garantit le nettoyage même en cas d'échec de capture
    assert calls == ["js", "js"]


def test_attach_screenshot_without_banner_never_touches_the_page(tmp_path, monkeypatch):
    """Sans bandeau demandé, la capture n'injecte aucun JavaScript dans la
    page: un evaluate piégé qui lèverait à la moindre injection le prouve
    par construction."""

    def forbidden_evaluate(client, expression, **kwargs):  # pragma: no cover
        raise AssertionError("aucun JS ne doit être injecté sans banner")

    def fake_screenshot(client, path, *, full_page=False):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as stream:
            stream.write(b"\x89PNG\r\n\x1a\n")
        return {"path": path, "bytes": 8}

    monkeypatch.setattr(e2e.js, "evaluate", forbidden_evaluate)
    monkeypatch.setattr(e2e.capture, "screenshot", fake_screenshot)

    artifact = e2e.attach_screenshot(_case(tmp_path), object(), "final")

    #: la capture aboutit alors que tout evaluate aurait levé: zéro JS injecté
    assert artifact["type"] == "screenshot"
