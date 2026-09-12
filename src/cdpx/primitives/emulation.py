"""Device, network, and CPU emulation profiles."""

from __future__ import annotations

from typing import Any

from cdpx.action_model import VIEWPORT_PROFILES
from cdpx.client import CDPClient

PRESETS: dict[str, dict[str, Any]] = {
    "desktop": {
        "metrics": {
            "width": 1440,
            "height": 900,
            "deviceScaleFactor": 1,
            "mobile": False,
        },
    },
    "mobile": {
        "metrics": {
            "width": 390,
            "height": 844,
            "deviceScaleFactor": 3,
            "mobile": True,
        },
        "ua": "cdpx-mobile/1.0",
    },
    "slow-3g": {
        "network": {
            "offline": False,
            "latency": 400,
            "downloadThroughput": 50 * 1024,
            "uploadThroughput": 50 * 1024,
        }
    },
    "cpu-4x": {"cpu": 4},
}


def set_viewport(client: CDPClient, profile: str) -> dict[str, Any]:
    """Applies the device-metrics part of a viewport profile (desktop or
    mobile) without network/CPU/UA side effects. Scenario steps use it to
    switch viewport mid-journey — e.g. capture the desktop and mobile
    variants of one evidence outcome in a single run."""
    if profile not in VIEWPORT_PROFILES:
        raise ValueError(f"unknown viewport profile: {profile}")
    client.send("Network.enable")
    client.send("Emulation.setDeviceMetricsOverride", dict(PRESETS[profile]["metrics"]))
    return {"profile": profile, "applied": True}


def emulate(client: CDPClient, preset: str | None = None, reset: bool = False) -> dict[str, Any]:
    if reset:
        client.send("Emulation.clearDeviceMetricsOverride")
        # empty userAgent = Chrome restores the default UA (verified e2e); without
        # this call, the mobile preset's UA survived the reset.
        client.send("Emulation.setUserAgentOverride", {"userAgent": ""})
        client.send(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": 0,
                "downloadThroughput": -1,
                "uploadThroughput": -1,
            },
        )
        client.send("Emulation.setCPUThrottlingRate", {"rate": 1})
        return {"reset": True}
    if preset not in PRESETS:
        raise ValueError(f"unknown preset: {preset}")
    spec = PRESETS[preset]
    client.send("Network.enable")
    if "metrics" in spec:
        client.send("Emulation.setDeviceMetricsOverride", spec["metrics"])
    if "ua" in spec:
        client.send("Emulation.setUserAgentOverride", {"userAgent": spec["ua"]})
    if "network" in spec:
        client.send("Network.emulateNetworkConditions", spec["network"])
    if "cpu" in spec:
        client.send("Emulation.setCPUThrottlingRate", {"rate": spec["cpu"]})
    return {"preset": preset, "applied": True}
