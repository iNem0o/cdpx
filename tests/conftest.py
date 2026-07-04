"""Fixtures pytest.

Deux serveurs locaux, portée `function` (isolation totale entre tests,
ports éphémères -> zéro collision, parallélisable):
- mock: un faux Chrome scriptable (cdpx.testing.mock_cdp.MockCDP),
- fixtures_http: le site témoin statique (cdpx.testing.fixture_server).
"""

import pytest

from cdpx.testing.fixture_server import FixtureServer
from cdpx.testing.mock_cdp import MockCDP


@pytest.fixture()
def mock():
    with MockCDP() as m:
        yield m


@pytest.fixture()
def fixtures_http():
    with FixtureServer() as srv:
        yield srv
