#!/usr/bin/env python3
"""Vérifie le contenu public du wheel et du sdist avant publication."""

from __future__ import annotations

import email
import json
import tarfile
import zipfile
from pathlib import Path

from cdpx import __version__

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def _single(pattern: str) -> Path:
    matches = sorted(DIST.glob(pattern))
    if len(matches) != 1:
        raise AssertionError(f"attendu un artefact {pattern}, trouvé: {matches}")
    return matches[0]


def _assert_no_private_paths(paths: set[str]) -> None:
    forbidden = (".proof/", ".gitlab-ci.yml", "AGENTS.md", "article/", "presentation/")
    for path in paths:
        relative = path.split("/", 1)[1] if "/" in path and path.startswith("cdpx-") else path
        assert not relative.startswith(forbidden), (
            f"contenu non public dans la distribution: {path}"
        )


def verify_wheel(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        license_name = next(name for name in names if name.endswith(".dist-info/licenses/LICENSE"))
        metadata = email.message_from_bytes(archive.read(metadata_name))
        top_levels = {name.split("/", 1)[0] for name in names if "/" in name}
        dist_info = metadata_name.split("/", 1)[0]

        assert top_levels <= {"cdpx", dist_info}, f"module inattendu dans le wheel: {top_levels}"
        assert "cdpx/__init__.py" in names
        assert metadata["Name"] == "cdpx"
        assert metadata["Version"] == __version__
        assert metadata["License-Expression"] == "MIT"
        assert "LICENSE" in metadata.get_all("License-File", [])
        assert metadata["Description-Content-Type"].startswith("text/markdown")
        assert "# cdpx" in metadata.get_payload()
        assert archive.read(license_name) == (ROOT / "LICENSE").read_bytes()
        _assert_no_private_paths(names)

    return {"path": str(path), "files": len(names), "metadata": metadata_name}


def verify_sdist(path: Path) -> dict[str, object]:
    with tarfile.open(path, "r:gz") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
        roots = {name.split("/", 1)[0] for name in names}
        assert roots == {f"cdpx-{__version__}"}, f"racine sdist inattendue: {roots}"
        root = next(iter(roots))
        required = {
            "LICENSE",
            "README.md",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "CODE_OF_CONDUCT.md",
            "SUPPORT.md",
            "pyproject.toml",
            "MANIFEST.in",
            "scripts/verify_dist.py",
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
            "src/cdpx/__init__.py",
            "tests/fixtures/profiler/LICENSE.SYMFONY",
        }
        missing = {name for name in required if f"{root}/{name}" not in names}
        assert not missing, f"fichiers absents du sdist: {sorted(missing)}"
        _assert_no_private_paths(names)

    return {"path": str(path), "files": len(names), "root": root}


def main() -> int:
    wheel = verify_wheel(_single(f"cdpx-{__version__}-*.whl"))
    sdist = verify_sdist(_single(f"cdpx-{__version__}.tar.gz"))
    print(json.dumps({"ok": True, "wheel": wheel, "sdist": sdist}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
