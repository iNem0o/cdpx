"""Single registry of every file that pins the release version.

``tools.bump`` rewrites these tokens and
``tests/test_packaging.py::test_release_version_pins_move_together``
asserts their presence, so the rewriter and the guard cannot diverge.
"""

from __future__ import annotations


def version_pins(version: str) -> dict[str, list[str]]:
    """Map each pinned file to the exact tokens carrying ``version``."""
    v = version
    return {
        "pyproject.toml": [f'version = "{v}"'],
        ".github/workflows/ci.yml": [f'--build-arg "VERSION={v}"'],
        "README.md": [f"**Version {v} "],
        "cdpx": [f'LAUNCHER_VERSION="{v}"'],
        "docker-bake.hcl": [f'default = "{v}"'],
        "packaging/install": [f"VERSION=${{CDPX_VERSION:-v{v}}}"],
        "packaging/Dockerfile.embedded": [f"FROM ghcr.io/inem0o/cdpx:{v} AS cdpx"],
        "packaging/compose.sidecar.yml": [f"image: ghcr.io/inem0o/cdpx:{v}"],
        "tests/test_launcher.sh": [f'"launcher_version":"{v}"'],
        "tests/test_packaging.py": [f'assert __version__ == "{v}"'],
        "docs/INSTALLATION.md": [f"--version v{v}", f"FROM ghcr.io/inem0o/cdpx:{v}"],
        "site/index.html": [f"{v} · pre-1.0 beta", f"Version {v} ·"],
        "uv.lock": [f'name = "cdpx"\nversion = "{v}"'],
    }
