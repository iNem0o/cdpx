"""E2E helpers testable without Chrome: ephemeral proof banner."""

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
    """The proof banner's wording is neutralized (JSON escaping +
    textContent) and the matching cleanup script targets the same node:
    the banner can neither inject HTML nor survive the capture."""
    script = e2e.banner_inject_script('Form submitted — state "final"')

    #: the wording is passed as JSON and assigned via textContent: no injection
    assert '\\"final\\"' in script
    assert "textContent" in script and "innerHTML" not in script
    assert "cdpx-proof-banner" in script
    #: the banner is fixed bottom: it never alters the measured layout
    assert "position:fixed" in script

    cleanup = e2e.banner_cleanup_script()
    #: the cleanup script removes exactly the node the injection created
    assert "remove()" in cleanup and "cdpx-proof-banner" in cleanup


def test_attach_screenshot_injects_then_always_removes_the_banner(tmp_path, monkeypatch):
    """The capture with banner follows the strict order inject → capture →
    cleanup: the screenshot shows the banner but the page keeps no trace
    of it afterward."""
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

    artifact = e2e.attach_screenshot(_case(tmp_path), object(), "final", banner="Step 3")

    #: injection before capture, removal after: the page becomes intact again
    assert calls == ["inject", "capture", "cleanup"]
    assert artifact["type"] == "screenshot"


def test_attach_screenshot_removes_the_banner_even_when_capture_fails(tmp_path, monkeypatch):
    """A capture failure never leaves the page polluted: the error
    propagates to the caller but the banner is removed anyway."""
    calls = []

    monkeypatch.setattr(e2e.js, "evaluate", lambda client, expression, **kwargs: calls.append("js"))

    def broken_screenshot(client, path, *, full_page=False):
        raise RuntimeError("capture down")

    monkeypatch.setattr(e2e.capture, "screenshot", broken_screenshot)

    #: the capture failure is not swallowed: the caller knows the proof is missing
    with pytest.raises(RuntimeError, match="capture down"):
        e2e.attach_screenshot(_case(tmp_path), object(), "final", banner="Step 3")

    #: the finally guarantees cleanup even when the capture fails
    assert calls == ["js", "js"]


def test_attach_screenshot_without_banner_never_touches_the_page(tmp_path, monkeypatch):
    """With no banner requested, the capture injects no JavaScript into the
    page: a trapped evaluate that would raise on the slightest injection
    proves it by construction."""

    def forbidden_evaluate(client, expression, **kwargs):  # pragma: no cover
        raise AssertionError("no JS must be injected without a banner")

    def fake_screenshot(client, path, *, full_page=False):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as stream:
            stream.write(b"\x89PNG\r\n\x1a\n")
        return {"path": path, "bytes": 8}

    monkeypatch.setattr(e2e.js, "evaluate", forbidden_evaluate)
    monkeypatch.setattr(e2e.capture, "screenshot", fake_screenshot)

    artifact = e2e.attach_screenshot(_case(tmp_path), object(), "final")

    #: the capture succeeds even though any evaluate would have raised: zero JS injected
    assert artifact["type"] == "screenshot"
