"""Blocking Shopware 6.7 profiler runtime proof."""

import json
import os
from pathlib import Path

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
        "DAL titles, source locations, active rules, cache tags and feature flags are structured.",
        "The default selection skips Cart while an explicit request parses its real collector.",
        "A real Shopware cart and tagged collector/processor expose bounded machine-readable data.",
    ],
)
def test_profiler_reads_real_shopware_connection_collector(runtime_session, evidence_case):
    assert SHOPWARE_URL is not None
    manifest, manifest_path = runtime_session
    default_proc = run_cli(
        manifest,
        manifest_path,
        "--timeout",
        "30",
        "profiler",
        f"{SHOPWARE_URL}/cdpx-profiler",
        "--settle",
        "0.8",
        timeout=180,
    )
    attach_cli_run(evidence_case, "Shopware default profiler CLI", default_proc)
    result = successful_json(default_proc)
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
        default_requested_collectors = json.loads(
            js.evaluate(
                client,
                "JSON.stringify(performance.getEntriesByType('resource')"
                ".map(entry => new URL(entry.name).searchParams.get('panel'))"
                ".filter(Boolean))",
            )
        )
        js.evaluate(client, "performance.clearResourceTimings()")

    cart_proc = run_cli(
        manifest,
        manifest_path,
        "--timeout",
        "30",
        "profiler",
        f"{SHOPWARE_URL}/cdpx-cart-profiler",
        "--panels",
        "shopware_cart",
        "--settle",
        "0.8",
        timeout=180,
    )
    attach_cli_run(evidence_case, "Shopware Cart profiler CLI", cart_proc)
    cart_result = successful_json(cart_proc)
    assert isinstance(cart_result, dict)
    with CDPClient(manifest.websocket_url, timeout=30) as client:
        cart_requested_collectors = json.loads(
            js.evaluate(
                client,
                "JSON.stringify(performance.getEntriesByType('resource')"
                ".map(entry => new URL(entry.name).searchParams.get('panel'))"
                ".filter(Boolean))",
            )
        )
        attach_screenshot(evidence_case, client, "Real Shopware Cart profiler target")

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
    feature_flags = result["panels"]["shopware_feature_flags"]
    deterministic_flag = next(
        item for item in feature_flags["list"] if item["name"] == "CDPX_E2E_FEATURE"
    )
    assert deterministic_flag == {
        "name": "CDPX_E2E_FEATURE",
        "active": True,
        "default": False,
        "major": False,
        "description": "Deterministic cdpx E2E feature flag",
    }
    assert "shopware_cart" not in result["panels"]
    assert "db" not in default_requested_collectors
    assert (
        "Shopware\\Core\\Profiling\\Subscriber\\CartDataCollectorSubscriber"
        not in default_requested_collectors
    )
    assert {
        "request",
        "app.connection_collector",
        "Shopware\\Core\\Profiling\\Subscriber\\ActiveRulesDataCollectorSubscriber",
        "Shopware\\Core\\Profiling\\Subscriber\\CacheTagCollectorSubscriber",
        "feature_flag",
    } <= set(default_requested_collectors)

    cart_collector = "Shopware\\Core\\Profiling\\Subscriber\\CartDataCollectorSubscriber"
    assert cart_result["token_present"] is True and "token" not in cart_result
    assert cart_result["profile"]["probed"] is True
    assert cart_result["panels"].keys() == {"shopware_cart"}
    cart = cart_result["panels"]["shopware_cart"]
    assert cart["available"] is True
    assert cart["present"] is True
    assert cart["item_count"] == 1
    assert cart["line_items"]["total"] == 1
    assert cart["line_items"]["items"] == [
        {
            "quantity": 2,
            "label": "cdpx deterministic item",
            "type": "custom",
            "unit_price_display": cart["line_items"]["items"][0]["unit_price_display"],
            "total_price_display": cart["line_items"]["items"][0]["total_price_display"],
        }
    ]
    assert cart["line_items"]["items"][0]["unit_price_display"]
    assert cart["line_items"]["items"][0]["total_price_display"]
    assert cart["totals"]["subtotal_display"]
    assert cart["totals"]["total_display"]
    collector = next(
        item
        for item in cart["pipeline"]["collectors"]["items"]
        if item["service_id"] == "cdpx.e2e.cart.collector"
    )
    processor = next(
        item
        for item in cart["pipeline"]["processors"]["items"]
        if item["service_id"] == "cdpx.e2e.cart.processor"
    )
    assert collector["priority"] == 1234
    assert processor["priority"] == 4321
    assert cart_collector in cart_requested_collectors
    assert "shopware_cart" not in cart_requested_collectors
    serialized_cart = json.dumps(cart_result)
    assert "CDPX-CART-PAYLOAD-MUST-NOT-LEAK" not in serialized_cart
    assert "sf-dump" not in serialized_cart

    if evidence_case is not None:
        evidence_case.attach_json("Shopware profiler result", result, "shopware-profiler.json")
        evidence_case.attach_json(
            "Shopware Cart profiler result", cart_result, "shopware-cart-profiler.json"
        )
        evidence_case.attach_json(
            "Shopware adaptive collector probe",
            {
                "default_requested_collectors": default_requested_collectors,
                "cart_requested_collectors": cart_requested_collectors,
                "runtime_identity": runtime_identity,
            },
            "shopware-collector-probe.json",
        )


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="scenario-run",
    scenario_id="orchestration-control.target-shopware-profiler-request",
    target="shopware",
    proof_level="runtime",
    proves=[
        "A scenario selects the profiler token emitted by a real Shopware Fetch request.",
        "The scenario fetches only its explicit panels, including the opt-in Cart panel.",
    ],
)
def test_scenario_targets_real_shopware_fetch_profiler(runtime_session, evidence_case, tmp_path):
    assert SHOPWARE_URL is not None
    manifest, manifest_path = runtime_session
    scenario_path = tmp_path / "targeted-shopware-profiler.yml"
    scenario_path.write_text(
        json.dumps(
            {
                "schema": "cdpx.scenario/v1",
                "name": "targeted-shopware-cart-profiler",
                "context": {"base_url": SHOPWARE_URL},
                "steps": [
                    {"goto": "/cdpx-profiler", "label": "shopware-document"},
                    {
                        "label": "cart-fetch",
                        "eval": (
                            "fetch('/cdpx-cart-profiler').then(response => response.text())"
                            ".then(() => 'cart-loaded')"
                        ),
                        "capture": [
                            {
                                "profiler": {
                                    "panels": [
                                        "time",
                                        "db",
                                        "shopware_rules",
                                        "shopware_cache_tags",
                                        "shopware_cart",
                                    ],
                                    "request": {
                                        "url_prefix": "/cdpx-cart-profiler",
                                        "resource_type": "fetch",
                                        "method": "GET",
                                    },
                                }
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    scenario_proc = run_cli(
        manifest,
        manifest_path,
        "--timeout",
        "30",
        "scenario",
        "run",
        str(scenario_path),
        "--settle",
        "0.8",
        timeout=180,
    )
    attach_cli_run(evidence_case, "Targeted Shopware profiler scenario", scenario_proc)
    scenario_result = successful_json(scenario_proc)
    assert isinstance(scenario_result, dict)
    assert scenario_result["verdict"] == "pass"
    (scenario_artifact,) = scenario_result["artifacts"]
    scenario_profiler = json.loads(Path(scenario_artifact["path"]).read_text(encoding="utf-8"))

    assert scenario_profiler["panels"].keys() == {
        "time",
        "db",
        "shopware_rules",
        "shopware_cache_tags",
        "shopware_cart",
    }
    assert scenario_profiler["panels"]["shopware_cart"]["present"] is True
    assert scenario_profiler["selection"] == {
        "mode": "request_selector",
        "criteria": {
            "url_prefix": "/cdpx-cart-profiler",
            "resource_type": "fetch",
            "method": "GET",
        },
        "matched": {"resource_type": "fetch", "method": "GET"},
    }
    serialized = json.dumps(scenario_profiler)
    assert "CDPX-CART-PAYLOAD-MUST-NOT-LEAK" not in serialized
    if evidence_case is not None:
        evidence_case.attach_json(
            "Targeted Shopware scenario profiler",
            scenario_profiler,
            "shopware-targeted-scenario-profiler.json",
        )
