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
        "extra_hosts": [],
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


def test_extra_hosts_emit_add_host_arguments(tmp_path: Path):
    entries = [
        "app.stack.local:172.20.0.10",
        "host.docker.internal:host-gateway",
        "v6.local:2001:db8::1",
    ]
    (tmp_path / "cdpx.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "cdpx/v1",
                "runtime": {"network": "network:stack_default", "extra_hosts": entries},
            }
        ),
        encoding="utf-8",
    )

    plan = compile_plan(tmp_path, tmp_path / ".cdpx/runtime/plan")

    assert plan["effective"]["runtime"]["extra_hosts"] == entries
    lines = (tmp_path / ".cdpx/runtime/plan/docker.args").read_text().splitlines()
    assert lines.count("--add-host") == len(entries)
    pairs = list(zip(lines, lines[1:], strict=False))
    for entry in entries:
        assert ("--add-host", entry) in pairs


def test_interpolation_resolves_the_calling_environment(tmp_path: Path):
    (tmp_path / "cdpx.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "cdpx/v1",
                "runtime": {
                    "network": "network:${STACK_NET}",
                    "extra_hosts": ["${APP_HOST:-app.local}:${APP_IP}"],
                },
                "environment": {"set": {"PRICE": "cost is $$5"}},
            }
        ),
        encoding="utf-8",
    )
    environ = {"STACK_NET": "stack_default", "APP_IP": "172.20.0.10"}

    plan = compile_plan(tmp_path, tmp_path / ".cdpx/runtime/plan", environ=environ)

    assert plan["effective"]["runtime"]["network"] == "network:stack_default"
    assert plan["effective"]["runtime"]["extra_hosts"] == ["app.local:172.20.0.10"]
    assert plan["effective"]["environment"]["set"]["PRICE"] == "cost is $5"
    docker_args = (tmp_path / ".cdpx/runtime/plan/docker.args").read_text()
    assert "app.local:172.20.0.10" in docker_args


def test_interpolation_distinguishes_empty_from_unset(tmp_path: Path):
    (tmp_path / "cdpx.yaml").write_text(
        yaml.safe_dump(
            {"environment": {"set": {"PLAIN": "${VAR}", "DEFAULTED": "${VAR:-fallback}"}}}
        ),
        encoding="utf-8",
    )

    configuration = load_configuration(tmp_path, environ={"VAR": ""})

    assert configuration["environment"]["set"] == {"PLAIN": "", "DEFAULTED": "fallback"}


def test_interpolation_never_touches_keys(tmp_path: Path):
    # A placeholder-looking key must reach validation uninterpolated: were
    # keys resolved, "${NAME}" would become the valid name "SAFE" and load.
    (tmp_path / "cdpx.yaml").write_text(
        yaml.safe_dump({"environment": {"set": {"${NAME}": "value"}}}), encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match=r"invalid environment name: '\$\{NAME\}'"):
        load_configuration(tmp_path, environ={"NAME": "SAFE"})


def test_interpolated_values_change_the_fingerprint(tmp_path: Path):
    (tmp_path / "cdpx.yaml").write_text(
        yaml.safe_dump({"runtime": {"network": "network:${STACK_NET}"}}), encoding="utf-8"
    )
    output = tmp_path / ".cdpx/runtime/plan"

    first = compile_plan(tmp_path, output, environ={"STACK_NET": "alpha"})
    second = compile_plan(tmp_path, output, environ={"STACK_NET": "beta"})

    assert first["fingerprint"] != second["fingerprint"]


@pytest.mark.parametrize(
    "document,error",
    [
        ({"unknown": True}, "unknown keys"),
        ({"runtime": {"network": "network:bad/name"}}, "runtime.network"),
        ({"runtime": {"extra_hosts": "app.local:1.2.3.4"}}, "list required"),
        ({"runtime": {"extra_hosts": ["nocolon"]}}, "invalid hostname"),
        ({"runtime": {"extra_hosts": ["bad host:1.2.3.4"]}}, "invalid hostname"),
        ({"runtime": {"extra_hosts": ["app.local:999.0.0.1"]}}, "host-gateway"),
        (
            {"runtime": {"extra_hosts": ["a.local:1.2.3.4", "A.local:5.6.7.8"]}},
            "duplicate hostname",
        ),
        (
            {"runtime": {"network": "container:web", "extra_hosts": ["a.local:host-gateway"]}},
            "not allowed with",
        ),
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


@pytest.mark.parametrize(
    "document,environ,error",
    [
        (
            {"runtime": {"network": "network:${MISSING_NET}"}},
            {},
            "undefined environment variable: MISSING_NET",
        ),
        ({"runtime": {"network": "${BAD"}}, {}, "malformed placeholder"),
        ({"environment": {"set": {"MODE": "a $ b"}}}, {}, "malformed placeholder"),
        ({"runtime": {"network": "network:${EVIL}"}}, {"EVIL": "a\nb"}, "runtime.network"),
        (
            {"runtime": {"extra_hosts": ["${EVIL_HOST}"]}},
            {"EVIL_HOST": "x:1.1.1.1\n--privileged"},
            "runtime.extra_hosts",
        ),
    ],
)
def test_interpolation_fails_closed(
    tmp_path: Path, document: dict[str, object], environ: dict[str, str], error: str
):
    (tmp_path / "cdpx.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=error):
        load_configuration(tmp_path, environ=environ)
