"""Real Docker E2E for runtime.extra_hosts and environment interpolation.

Compiles a real plan whose network name and extra hosts come from the
calling environment, then starts a real container from the emitted
docker arguments and asserts in-container hostname resolution — names
the container could never obtain from the host `/etc/hosts`.
"""

import json
import os
import shutil
import subprocess
import uuid
from ipaddress import IPv4Address
from pathlib import Path

import pytest
import yaml

from cdpx.runtime_config import compile_plan

DOCKER = shutil.which("docker")
IMAGE = os.environ.get("CDPX_E2E_IMAGE")

if DOCKER is None:
    pytest.fail("Docker required for cdpx runtime-network e2e", pytrace=False)
if not IMAGE:
    pytest.fail("CDPX_E2E_IMAGE required (run through ./dev)", pytrace=False)


def docker(*arguments: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run([DOCKER, *arguments], capture_output=True, text=True, timeout=timeout)


@pytest.fixture()
def stack_network():
    """A disposable named network standing in for a development stack."""

    name = f"cdpx-e2e-{uuid.uuid4().hex[:12]}"
    created = docker("network", "create", name)
    if created.returncode != 0:
        pytest.fail(f"cannot create docker network: {created.stderr}", pytrace=False)
    yield name
    docker("network", "rm", name)


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="ship-runtime",
    scenario_id="harness-proof-cockpit.resolve-stack-hosts-in-real-runtime",
    proves=[
        "An interpolated network name places the runtime container on the stack network.",
        "extra_hosts entries resolve inside the container to their declared targets.",
        "host-gateway resolves to the Docker host, distinct from static entries.",
    ],
)
def test_extra_hosts_resolve_inside_a_real_container_on_an_interpolated_network(
    tmp_path: Path, stack_network: str, evidence_case
):
    if docker("image", "inspect", IMAGE).returncode != 0:
        pytest.fail(f"image {IMAGE} unavailable (run ./dev setup)", pytrace=False)
    (tmp_path / "cdpx.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": "cdpx/v1",
                "runtime": {
                    "network": "network:${CDPX_E2E_STACK_NET}",
                    "extra_hosts": [
                        "${CDPX_E2E_APP_HOST:-app.e2e.local}:host-gateway",
                        "api.e2e.local:203.0.113.7",
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    plan = compile_plan(
        tmp_path,
        tmp_path / ".cdpx/runtime/plan",
        environ={"CDPX_E2E_STACK_NET": stack_network},
    )

    assert plan["effective"]["runtime"]["network"] == f"network:{stack_network}"
    arguments = (tmp_path / ".cdpx/runtime/plan/docker.args").read_text().splitlines()
    assert "--network" in arguments and stack_network in arguments

    # The compiled arguments alone must place the container on the
    # interpolated network (docker run fails on an unknown name) and
    # register both declared hostnames.
    probe = (
        "import json, socket;"
        "print(json.dumps({"
        "'api': socket.gethostbyname('api.e2e.local'),"
        "'app': socket.gethostbyname('app.e2e.local')}))"
    )
    argv = ["docker", "run", "--rm", *arguments, "--entrypoint", "python", IMAGE, "-c", probe]
    proc = docker(*argv[1:], timeout=120.0)
    if evidence_case is not None:
        evidence_case.attach_command_output(
            "In-container hostname resolution",
            argv,
            proc.stdout,
            proc.stderr,
            proc.returncode,
        )

    assert proc.returncode == 0, proc.stderr
    resolved = json.loads(proc.stdout.strip().splitlines()[-1])
    assert resolved["api"] == "203.0.113.7"
    gateway = IPv4Address(resolved["app"])
    assert gateway != IPv4Address("203.0.113.7")
