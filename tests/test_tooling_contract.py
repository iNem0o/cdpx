from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import yaml


def test_every_new_surface_has_user_and_integrator_documentation():
    catalog = yaml.safe_load(Path("docs/surfaces.yaml").read_text(encoding="utf-8"))

    assert catalog["schema"] == "cdpx.documentation/v1"
    identifiers: set[str] = set()
    for surface in catalog["surfaces"]:
        assert surface["id"] not in identifiers
        identifiers.add(surface["id"])
        for field in ("implementation", "user", "integrator"):
            path = Path(surface[field])
            assert path.is_file(), f"{surface['id']}: missing {field}: {path}"
        assert Path(surface["user"]).suffix in {".md", ".json"}
        assert Path(surface["integrator"]).suffix in {".md", ".json", ".yml", ".embedded"}


def test_config_schema_names_every_supported_key():
    schema = json.loads(Path("schemas/cdpx.schema.json").read_text(encoding="utf-8"))
    source = Path("src/cdpx/runtime_config.py").read_text(encoding="utf-8")

    assert schema["$id"].endswith("/schema/cdpx-v1.json")
    for key in (
        "network",
        "extra_hosts",
        "idle_timeout",
        "shm_size",
        "required",
        "optional",
        "set",
        "source",
        "target",
        "read_only",
        "ttl",
        "origins",
    ):
        assert f'"{key}"' in source
        assert key in json.dumps(schema)


def test_scenario_and_fragment_schemas_publish_the_composition_contract():
    scenario = json.loads(Path("schemas/scenario-v1.json").read_text(encoding="utf-8"))
    fragment = json.loads(Path("schemas/scenario-fragment-v1.json").read_text(encoding="utf-8"))
    assert scenario["$id"].endswith("/schema/scenario-v1.json")
    assert fragment["$id"].endswith("/schema/scenario-fragment-v1.json")
    assert scenario["properties"]["schema"]["const"] == "cdpx.scenario/v1"
    assert fragment["properties"]["schema"]["const"] == "cdpx.scenario-fragment/v1"
    assert scenario["properties"]["steps"]["items"]["$ref"].endswith(
        "scenario-fragment-v1.json#/$defs/step"
    )
    include = fragment["$defs"]["includeStep"]["properties"]["include"]
    assert include["additionalProperties"] is False
    assert set(include["properties"]) == {"path", "as"}
    assert "with" not in include["properties"]
    frame_type = fragment["$defs"]["frameTypeStep"]["properties"]["frame_type"]
    assert frame_type["additionalProperties"] is False
    assert "candidates" in frame_type["properties"]
    assert frame_type["properties"]["key_delay_ms"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 250,
    }
    delay_contract = frame_type["allOf"][0]
    assert delay_contract["if"]["required"] == ["key_delay_ms"]
    assert delay_contract["if"]["properties"]["key_delay_ms"] == {"minimum": 1}
    assert delay_contract["then"]["required"] == ["mode"]
    assert delay_contract["then"]["properties"]["mode"] == {"const": "key_events"}
    candidate = frame_type["properties"]["candidates"]["items"]
    assert set(candidate["required"]) == {"selector", "frame_origin", "secret_ref"}
    assert candidate["properties"]["frame_origin"] == frame_type["properties"]["frame_origin"]


def test_portable_scripts_are_posix_and_shellcheck_clean():
    scripts = [
        Path("cdpx"),
        Path("dev"),
        Path("packaging/install"),
        Path("packaging/native-python"),
        Path("packaging/native-chromium"),
        Path("packaging/native-certutil"),
        Path("packaging/native-cdpx"),
        Path("packaging/embedded-install"),
        Path("tests/test_launcher.sh"),
        Path("tests/e2e/run_framework_suite"),
    ]
    for script in scripts:
        assert script.read_text(encoding="utf-8").startswith("#!/bin/sh")
        assert os.access(script, os.X_OK), f"not executable: {script}"

    subprocess.run(["shellcheck", *map(str, scripts)], check=True)


def test_embedded_installer_resolves_the_public_bundle_symlink(tmp_path):
    bundle = tmp_path / "opt" / "cdpx"
    bundle_bin = bundle / "bin"
    bundle_bin.mkdir(parents=True)

    installer = bundle_bin / "embedded-install"
    installer.write_bytes(Path("packaging/embedded-install").read_bytes())
    installer.chmod(0o755)
    (bundle_bin / "native-cdpx").touch(mode=0o755)
    (bundle / "install").symlink_to("bin/embedded-install")

    target = tmp_path / "usr" / "local" / "bin" / "cdpx"
    completed = subprocess.run(
        [str(bundle / "install"), "--link", str(target)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "installed": "embedded",
        "path": str(target),
    }
    assert target.is_symlink()
    assert os.readlink(target) == str(bundle_bin / "native-cdpx")


def test_dockerfile_uses_one_pinned_multistage_toolchain():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    for image in re.findall(r"^ARG \w+_IMAGE=(.+)$", dockerfile, re.MULTILINE):
        assert re.fullmatch(r".+@sha256:[0-9a-f]{64}", image)
    for stage in ("dev", "ci", "runtime", "embedded"):
        assert re.search(rf"^FROM .+ AS {stage}$", dockerfile, re.MULTILINE)
    assert "python:3.14" in dockerfile
    assert "COPY --from=docker-cli" in dockerfile


def test_framework_gates_use_only_the_supervised_ci_chromium():
    helper_source = Path("src/cdpx/testing/e2e.py").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert 'PINNED_CHROMIUM = Path("/usr/bin/chromium")' in helper_source
    assert "chrome_bin=str(PINNED_CHROMIUM)" in helper_source
    assert '"chromium=' in dockerfile
    for suite in ("symfony", "shopware"):
        test_source = Path(f"tests/e2e/test_e2e_{suite}.py").read_text(encoding="utf-8")
        compose_source = Path(f"docker-compose.{suite}-e2e.yml").read_text(encoding="utf-8")
        assert "managed_runtime_session" in test_source
        assert "tests/e2e/run_framework_suite" in compose_source
        assert f'"{suite}"' in compose_source
        for bypass in ("subprocess.Popen", "discovery.new_tab", "shutil.which", "CHROME_BIN"):
            assert bypass not in test_source


def test_release_promotes_candidate_digest_and_never_publishes_python_package():
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "sha-$GITHUB_SHA-amd64" in ci
    assert "sha-$GITHUB_SHA-arm64" in ci
    assert "imagetools create" in ci
    assert "environment:\n      name: release" in release
    assert "imagetools create" in release
    assert "$REGISTRY_IMAGE@$digest" in release
    assert "docker buildx build" not in release
    assert "pypi" not in release.lower()
    assert "gh-action-pypi-publish" not in release
