"""PID 1 and diagnostics for a persistent workspace runtime container."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

from cdpx import session

STATE_SCHEMA = "cdpx.runtime/v1"


def runtime_root() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    return base / "cdpx"


def manifests() -> list[Path]:
    root = runtime_root()
    if not root.exists():
        return []
    return sorted(path for path in root.glob(f"*/{session.MANIFEST_NAME}") if path.is_file())


def active_sessions() -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for path in manifests():
        try:
            manifest = session.load_manifest(path)
            session.assert_session_active(manifest)
        except Exception:  # noqa: BLE001 - stale state is not an active capability
            continue
        active.append(
            {
                "session_id": manifest.session_id,
                "run_id": manifest.run_id,
                "target_id": manifest.target_id,
                "manifest": str(path),
                "expires_at": manifest.expires_at,
            }
        )
    return active


def status() -> dict[str, Any]:
    sessions = active_sessions()
    return {
        "schema": STATE_SCHEMA,
        "runtime_id": os.environ.get("CDPX_RUNTIME_ID", "standalone"),
        "workspace": os.environ.get("CDPX_WORKSPACE"),
        "image": os.environ.get("CDPX_IMAGE_REF"),
        "sessions": sessions,
        "active_sessions": len(sessions),
    }


def stop_sessions() -> None:
    for path in manifests():
        try:
            manifest = session.load_manifest(path)
            session.stop_session(
                path,
                run_id=manifest.run_id,
                target_id=manifest.target_id,
                timeout=10,
            )
        except Exception:  # noqa: BLE001 - container shutdown remains bounded
            with contextlib.suppress(OSError):
                session.remove_session_files(path)


def guardian(idle_timeout: float) -> int:
    stopping = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    idle_since = time.monotonic()
    try:
        while not stopping:
            if active_sessions():
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since >= idle_timeout:
                break
            time.sleep(0.5)
    finally:
        stop_sessions()
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cdpx.runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    guardian_parser = sub.add_parser("guardian")
    guardian_parser.add_argument("--idle-timeout", type=float, required=True)
    sub.add_parser("active-count")
    sub.add_parser("status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "guardian":
        return guardian(args.idle_timeout)
    if args.command == "active-count":
        print(len(active_sessions()))
        return 0
    print(json.dumps(status(), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
