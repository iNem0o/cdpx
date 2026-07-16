"""Profils d'émulation device, réseau et CPU."""

from __future__ import annotations

from typing import Any

from cdpx.client import CDPClient

PRESETS: dict[str, dict[str, Any]] = {
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


def emulate(client: CDPClient, preset: str | None = None, reset: bool = False) -> dict[str, Any]:
    if reset:
        client.send("Emulation.clearDeviceMetricsOverride")
        # userAgent vide = Chrome restaure l'UA par défaut (vérifié e2e); sans
        # cet appel, l'UA du preset mobile survivait au reset.
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
        raise ValueError(f"preset inconnu: {preset}")
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
