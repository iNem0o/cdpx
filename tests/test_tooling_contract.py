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
    ]
    for script in scripts:
        assert script.read_text(encoding="utf-8").startswith("#!/bin/sh")
        assert os.access(script, os.X_OK), f"not executable: {script}"

    subprocess.run(["shellcheck", *map(str, scripts)], check=True)


def test_dockerfile_uses_one_pinned_multistage_toolchain():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    for image in re.findall(r"^ARG \w+_IMAGE=(.+)$", dockerfile, re.MULTILINE):
        assert re.fullmatch(r".+@sha256:[0-9a-f]{64}", image)
    for stage in ("dev", "ci", "runtime", "embedded"):
        assert re.search(rf"^FROM .+ AS {stage}$", dockerfile, re.MULTILINE)
    assert "python:3.14" in dockerfile
    assert "COPY --from=docker-cli" in dockerfile


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
