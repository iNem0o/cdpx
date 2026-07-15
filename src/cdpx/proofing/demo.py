"""Démonstration CLI supervisée, enregistrée en .cast pendant ``make proof``.

Pilote le backend mock (aucun Chrome requis): la session supervisée, les
commandes réelles et leurs sorties JSON composent une preuve de démonstration
déterministe. Exit 0 seulement si toutes les commandes passent —
l'enregistreur exige un exit propre pour marquer le cast ``generated``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from cdpx.session import start_session, stop_session

DEMO_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("goto", "http://demo.test/"),
    ("tabs", "list"),
    ("eval", "document.title"),
)


def main() -> int:
    failures = 0
    with tempfile.TemporaryDirectory(prefix="cdpx-proof-demo-") as root:
        manifest, path = start_session(
            run_id=f"proof-demo-{os.getpid()}",
            authority="privileged",
            origins="http://*.test,http://127.0.0.1:*",
            ttl=300.0,
            owner_pid=os.getpid(),
            browser_kind="mock",
            root=root,
        )
        env = {
            **os.environ,
            "CDPX_SESSION": str(path),
            "CDPX_RUN_ID": manifest.run_id,
            "CDPX_TARGET": manifest.target_id,
        }
        print("# session mock supervisée — démonstration cdpx sans navigateur", flush=True)
        try:
            for command in DEMO_COMMANDS:
                print(f"$ cdpx {' '.join(command)}", flush=True)
                proc = subprocess.run(
                    [sys.executable, "-m", "cdpx.cli", *command],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=60.0,
                )
                print(proc.stdout.rstrip("\n"), flush=True)
                if proc.returncode != 0:
                    failures += 1
        finally:
            stop_session(path, run_id=manifest.run_id, target_id=manifest.target_id)
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - point d'entrée de démonstration
    raise SystemExit(main())
