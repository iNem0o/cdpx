from cdpx.testing.evidence import EvidenceCase, EvidenceSession, classify_nodeid


class FakeItem:
    def __init__(self, nodeid):
        self.nodeid = nodeid

    def get_closest_marker(self, name):
        return None


def test_classify_nodeid_by_test_area():
    assert classify_nodeid("tests/e2e/test_e2e_chrome.py::test_x") == "e2e"
    assert classify_nodeid("tests/test_cli.py::test_x") == "integration"
    assert classify_nodeid("tests/test_primitives.py::test_x") == "unit"


def test_evidence_case_attaches_artifacts(tmp_path):
    case = EvidenceCase(
        nodeid="tests/e2e/test_demo.py::test_records",
        root=tmp_path,
        suite="e2e",
        title="records",
    )
    screenshot = tmp_path / "shot.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nxxx")

    case.attach_screenshot(screenshot, "final")
    case.attach_json("payload", {"ok": True})
    case.attach_text("stdout", "hello")

    data = case.as_dict()
    assert data["artifacts"][0]["type"] == "screenshot"
    assert data["artifacts"][1]["type"] == "json"
    assert data["artifacts"][2]["type"] == "logs"
    assert all(tmp_path.as_posix() in artifact["path"] for artifact in data["artifacts"])


def test_evidence_session_writes_grouped_scenarios(tmp_path):
    session = EvidenceSession(tmp_path)
    session.case_for_item(FakeItem("tests/test_cli.py::test_cli_contract")).status = "passed"
    session.case_for_item(FakeItem("tests/e2e/test_e2e_chrome.py::test_page")).status = "passed"

    paths = session.write()

    assert sorted(path.rsplit("/", 1)[-1] for path in paths) == [
        "e2e-scenarios.json",
        "integration-scenarios.json",
    ]
