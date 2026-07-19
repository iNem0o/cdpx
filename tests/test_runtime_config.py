from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cdpx.runtime_config import ConfigurationError, compile_plan, load_configuration


def test_configuration_defaults_and_compiled_plan_are_private(tmp_path: Path):
    plan = compile_plan(tmp_path, tmp_path / ".cdpx/runtime/plan")

    assert plan["effective"]["runtime"] == {
        "network": "host",
        "idle_timeout": 86_400,
        "shm_size": 1_073_741_824,
    }
    output = tmp_path / ".cdpx/runtime/plan"
    assert json.loads((output / "plan.json").read_text()) == plan
    assert (output / "plan.json").stat().st_mode & 0o777 == 0o600


def test_configuration_forwards_only_declared_environment(tmp_path: Path):
    (tmp_path / "fixture").mkdir()
    (tmp_path / "cdpx.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "cdpx/v1",
                "runtime": {"network": "bridge", "idle_timeout": "10m"},
                "environment": {
                    "required": ["TOKEN"],
                    "optional": ["TRACE"],
                    "set": {"MODE": "test"},
                },
                "mounts": [{"source": "fixture", "target": "/fixture", "read_only": True}],
                "session": {"ttl": "2h", "origins": ["http://127.0.0.1:*"]},
            }
        ),
        encoding="utf-8",
    )

    plan = compile_plan(tmp_path, tmp_path / ".cdpx/runtime/plan")

    assert plan["config_trusted"] is True
    assert plan["effective"]["session"]["ttl"] == 7_200
    assert plan["effective"]["environment"]["required"] == ["TOKEN"]
    docker_args = (tmp_path / ".cdpx/runtime/plan/docker.args").read_text()
    assert f"source={tmp_path / 'fixture'},target=/fixture,readonly" in docker_args
    assert "TOKEN" in json.dumps(plan)
    assert "UNDECLARED" not in json.dumps(plan)


@pytest.mark.parametrize(
    "document,error",
    [
        ({"unknown": True}, "unknown keys"),
        ({"runtime": {"network": "network:bad/name"}}, "runtime.network"),
        ({"mounts": [{"source": "..", "target": "/data"}]}, "inside the workspace"),
        (
            {"mounts": [{"source": ".", "target": "/opt/cdpx/override"}]},
            "reserved runtime path",
        ),
        ({"session": {"ttl": "10s"}}, "between 60s"),
    ],
)
def test_configuration_fails_closed(tmp_path: Path, document: dict[str, object], error: str):
    (tmp_path / "cdpx.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=error):
        load_configuration(tmp_path)
