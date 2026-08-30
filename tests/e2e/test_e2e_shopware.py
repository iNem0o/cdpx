"""Blocking Shopware 6.7 profiler runtime proof."""

import json
import os

import pytest

from cdpx.client import CDPClient
from cdpx.primitives import js
from cdpx.testing.e2e import (
    attach_cli_run,
    attach_screenshot,
    managed_runtime_session,
    run_cli,
    successful_json,
    wait_for_http_200,
)

SHOPWARE_URL = os.environ.get("SHOPWARE_E2E_URL")

pytestmark = pytest.mark.skipif(
    not SHOPWARE_URL,
    reason="SHOPWARE_E2E_URL missing (run ./dev check)",
)


@pytest.fixture(scope="module")
def runtime_session(tmp_path_factory):
    assert SHOPWARE_URL is not None
    wait_for_http_200(
        f"{SHOPWARE_URL}/cdpx-profiler",
        label="Shopware app",
        timeout=180,
        interval=1,
    )
    runtime = tmp_path_factory.mktemp("cdpx-shopware-session")
    with managed_runtime_session(
        run_id="shopware-e2e",
        origin=SHOPWARE_URL,
        root=runtime,
    ) as session:
        yield session


@pytest.mark.scenario(
    feature="dev-profiler-diff",
    journey="read-profiler",
    scenario_id="dev-profiler-diff.read-shopware-profiler",
    target="shopware",
    proof_level="runtime",
    proves=[
        "Shopware 6.7 emits a real profiler token.",
        "The collector probe selects app.connection_collector without a speculative 404.",
        "DAL titles, source locations, active rules and cache tags are structured.",
    ],
)
def test_profiler_reads_real_shopware_connection_collector(runtime_session, evidence_case):
    assert SHOPWARE_URL is not None
    manifest, manifest_path = runtime_session
    proc = run_cli(
        manifest,
        manifest_path,
        "--timeout",
        "30",
        "profiler",
        f"{SHOPWARE_URL}/cdpx-profiler",
        "--panels",
        "db,router,shopware_rules,shopware_cache_tags",
        "--settle",
        "0.8",
        timeout=180,
    )
    attach_cli_run(evidence_case, "Shopware profiler CLI", proc)
    result = successful_json(proc)
    assert isinstance(result, dict)
    with CDPClient(manifest.websocket_url, timeout=30) as client:
        runtime_identity = json.loads(
            js.evaluate(
                client,
                "JSON.stringify((() => {"
                "const node = document.querySelector('#cdpx-shopware-profiler');"
                "return {title: document.title, middlewares: node?.dataset.middlewares || ''};"
                "})())",
            )
        )
        requested_collectors = json.loads(
            js.evaluate(
                client,
                "JSON.stringify(performance.getEntriesByType('resource')"
                ".map(entry => new URL(entry.name).searchParams.get('panel'))"
                ".filter(Boolean))",
            )
        )
        attach_screenshot(evidence_case, client, "Real Shopware profiler target")

    panel = result["panels"]["db"]
    profile = result["profile"]
    assert result["token_present"] is True and "token" not in result
    assert result["status"] == 200
    assert result["response_headers"]["sw-language-id"]
    assert result["response_headers"]["sw-currency-id"]
    assert runtime_identity["title"] == "cdpx Shopware profiler"
    assert runtime_identity["middlewares"].split(",") == [
        "Shopware\\Core\\Profiling\\Doctrine\\ProfilingMiddleware"
    ]
    assert profile["engine"] == "symfony_web_profiler"
    assert profile["probed"] is True
    assert profile["extensions"] == ["shopware"]
    assert profile["collectors"]["total"] > len(profile["collectors"]["items"])
    assert profile["collectors"]["truncated"] is True
    assert panel["available"] is True
    assert panel["queries"] >= 5
    assert panel["max_repetitions"] >= 5
    assert {"sql": "SELECT 1 /* cdpx-shopware-e2e */", "count": 5} in panel["repeated"]
    tagged = next(
        item for item in panel["tagged"] if "cdpx-shopware-e2e::search-ids" in item["tags"]
    )
    assert tagged["source"]["call"] == (
        "Shopware\\Core\\Framework\\DataAbstractionLayer\\EntityRepository->searchIds"
    )
    assert tagged["source"]["file"].endswith("/CdpxE2E/src/Controller/ProfilerController.php")
    assert result["panels"]["router"]["route"] == "frontend.cdpx.profiler"
    assert result["panels"]["shopware_rules"]["count"] >= 1
    assert result["panels"]["shopware_cache_tags"]["tags"] >= 1
    assert "db" not in requested_collectors
    assert {
        "request",
        "app.connection_collector",
        "Shopware\\Core\\Profiling\\Subscriber\\ActiveRulesDataCollectorSubscriber",
        "Shopware\\Core\\Profiling\\Subscriber\\CacheTagCollectorSubscriber",
    } <= set(requested_collectors)

    if evidence_case is not None:
        evidence_case.attach_json("Shopware profiler result", result, "shopware-profiler.json")
        evidence_case.attach_json(
            "Shopware adaptive collector probe",
            {
                "requested_collectors": requested_collectors,
                "runtime_identity": runtime_identity,
            },
            "shopware-collector-probe.json",
        )
