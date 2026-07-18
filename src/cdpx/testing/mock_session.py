"""Launch a supervised mock session in the foreground for ``make mock``."""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import threading
from pathlib import Path

from cdpx.session import export_lines, start_session, stop_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cdpx.testing.mock_session")
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--origins",
        default=os.environ.get(
            "CDPX_ORIGINS",
            "http://*.test,http://127.0.0.1:*",
        ),
    )
    parser.add_argument("--ttl", type=float, default=3600.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = args.run_id or f"mock-{os.getpid()}"
    manifest, path = start_session(
        run_id=run_id,
        authority="privileged",
        origins=args.origins,
        ttl=args.ttl,
        owner_pid=os.getpid(),
        browser_kind="mock",
    )
    print("Supervised mock session ready. Copy these exports:", flush=True)
    for line in export_lines(manifest, path):
        print(line, flush=True)
    print("cdpx goto http://demo.test/", flush=True)
    print("cdpx tabs list", flush=True)
    print("Ctrl-C stops the session and removes its files.", flush=True)

    stopped = threading.Event()

    def request_stop(_signum, _frame):
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while path.exists() and not stopped.wait(0.25):
            pass
    finally:
        if Path(path).exists():
            with contextlib.suppress(Exception):
                stop_session(
                    path,
                    run_id=manifest.run_id,
                    target_id=manifest.target_id,
                )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
