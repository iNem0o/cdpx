"""Fixtures pytest.

Deux serveurs locaux, portée `function` (isolation totale entre tests,
ports éphémères -> zéro collision, parallélisable):
- mock: un faux Chrome scriptable (cdpx.testing.mock_cdp.MockCDP),
- fixtures_http: le site témoin statique (cdpx.testing.fixture_server).
"""

import os

import pytest

from cdpx.testing.evidence import EvidenceSession
from cdpx.testing.fixture_server import FixtureServer
from cdpx.testing.mock_cdp import MockCDP


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        (
            "scenario(title=None, area=None, feature=None, journey=None, "
            "scenario_id=None, proves=None): "
            "proof report scenario metadata"
        ),
    )
    evidence_dir = config.getoption("--cdpx-evidence-dir", default=None) or os.environ.get(
        "CDPX_EVIDENCE_DIR"
    )
    if evidence_dir:
        suite = config.getoption("--cdpx-evidence-suite", default=None) or os.environ.get(
            "CDPX_EVIDENCE_SUITE"
        )
        config._cdpx_evidence = EvidenceSession(evidence_dir, suite_override=suite)
    else:
        config._cdpx_evidence = None


def pytest_addoption(parser):
    parser.addoption(
        "--cdpx-evidence-dir",
        action="store",
        default=None,
        help="write cdpx scenario evidence JSON and artifacts into this directory",
    )
    parser.addoption(
        "--cdpx-evidence-suite",
        action="store",
        default=None,
        help="override the proof evidence suite name for this pytest run",
    )


@pytest.fixture()
def evidence_case(request):
    session = getattr(request.config, "_cdpx_evidence", None)
    if session is None:
        return None
    return session.case_for_item(request.node)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    session = getattr(item.config, "_cdpx_evidence", None)
    if session is None:
        return
    if report.when == "call" or report.outcome in {"failed", "skipped"}:
        session.case_for_item(item).set_report(report)


def pytest_sessionfinish(session, exitstatus):
    evidence = getattr(session.config, "_cdpx_evidence", None)
    if evidence is not None:
        evidence.write()


@pytest.fixture()
def mock():
    with MockCDP() as m:
        yield m


@pytest.fixture()
def fixtures_http():
    with FixtureServer() as srv:
        yield srv
