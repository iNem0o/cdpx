"""Mechanical release preparation: move every version pin in one step.

``python -m tools.bump X.Y.Z`` rewrites each file listed in
``tools.release_pins`` from the current version to the target and stamps
the ``[Unreleased]`` changelog section with the target version and date.
The working tree is only modified once every pin has been located, so a
failed run changes nothing. Committing stays a separate, deliberate step
(``Prepare cdpx X.Y.Z``, see docs/RELEASING.md).
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import tomllib
from pathlib import Path

from tools.release_pins import version_pins

CHANGELOG = "CHANGELOG.md"
UNRELEASED_HEADING = "## [Unreleased]"


def current_version() -> str:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    return str(pyproject["project"]["version"])


def stamped_changelog(text: str, version: str, date: str) -> str:
    """Rename the ``[Unreleased]`` section, which must carry release notes."""
    if UNRELEASED_HEADING not in text:
        raise SystemExit(f"bump: {CHANGELOG} has no '{UNRELEASED_HEADING}' section")
    section = text.split(UNRELEASED_HEADING, 1)[1]
    body = section.split("\n## [", 1)[0]
    if not body.strip():
        raise SystemExit(
            f"bump: the '{UNRELEASED_HEADING}' section is empty, write release notes first"
        )
    return text.replace(UNRELEASED_HEADING, f"## [{version}] — {date}", 1)


def bump(target: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", target):
        raise SystemExit(f"bump: target version must be X.Y.Z, got {target!r}")
    current = current_version()
    if target == current:
        raise SystemExit(f"bump: already at {current}")

    old_pins = version_pins(current)
    new_pins = version_pins(target)
    rewrites: list[tuple[Path, str]] = []
    for source, old_tokens in old_pins.items():
        path = Path(source)
        text = path.read_text(encoding="utf-8")
        for old_token in old_tokens:
            if old_token not in text:
                raise SystemExit(f"bump: {source}: expected pin {old_token!r} not found")
        #: tokens of one file may overlap (site/index.html), so an earlier
        #: replacement can consume a later token; each replace is therefore
        #: best-effort and the rewrite is proven by the new tokens instead
        for old_token, new_token in zip(old_tokens, new_pins[source], strict=True):
            text = text.replace(old_token, new_token)
        for new_token in new_pins[source]:
            if new_token not in text:
                raise SystemExit(f"bump: {source}: pin {new_token!r} missing after rewrite")
        rewrites.append((path, text))

    date = datetime.date.today().isoformat()
    changelog = Path(CHANGELOG)
    stamped = stamped_changelog(changelog.read_text(encoding="utf-8"), target, date)
    rewrites.append((changelog, stamped))

    for path, text in rewrites:
        path.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "bumped": {"from": current, "to": target, "date": date},
                "files": [str(path) for path, _ in rewrites],
            },
            separators=(",", ":"),
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m tools.bump X.Y.Z", file=sys.stderr)
        return 2
    bump(args[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
