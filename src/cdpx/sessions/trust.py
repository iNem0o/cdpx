"""Per-session NSS trust store seeding for a disposable development Chrome.

Chrome reads CA trust from an NSS database under ``$HOME/.pki/nssdb``. A cdpx
session that must reach local self-signed HTTPS mounts a CA directory and seeds
a private database with it, without ever touching the user's real profile.

The module deliberately imports nothing from :mod:`cdpx.session` (only the
shared :class:`PolicyError`) to avoid a circular import; the small secure-mkdir
helper is replicated locally.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from cdpx.policy import PolicyError

# certutil -A imports only the first certificate of a file; CA bundles produced
# by traefik/step often concatenate several. Each PEM block is imported on its
# own; bytes outside a block (comments, blank lines) are ignored.
_PEM_BLOCK_RE = re.compile(
    rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
    re.DOTALL,
)


def _secure_mkdir(path: Path) -> None:
    """Create ``path`` at 0700, refusing to follow a pre-existing symlink."""
    if path.is_symlink():
        raise PolicyError(f"symbolic trust directory forbidden: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise PolicyError(f"trust directory required: {path}")
    path.chmod(0o700)


def _resolve_certutil(certutil: str | None) -> str:
    candidate = certutil or os.environ.get("CDPX_CERTUTIL") or shutil.which("certutil")
    if not candidate:
        raise PolicyError(
            "trust store requested but certutil unavailable; "
            "install libnss3-tools or set CDPX_CERTUTIL"
        )
    return candidate


def seed_trust_store(
    trust_dir: Path,
    home_dir: Path,
    certutil: str | None = None,
) -> int:
    """Seed a private NSS database under ``home_dir`` from ``trust_dir``.

    Every ``*.pem``/``*.crt`` file in ``trust_dir`` is split into individual
    certificate blocks and imported as a trusted CA. Any certutil failure or a
    total of zero imported certificates raises :class:`PolicyError` (fail
    closed). Returns the number of certificates imported.
    """
    tool = _resolve_certutil(certutil)
    nssdb = home_dir / ".pki" / "nssdb"
    _secure_mkdir(nssdb)
    db_arg = f"sql:{nssdb}"

    created = subprocess.run(
        [tool, "-d", db_arg, "-N", "--empty-password"],
        capture_output=True,
    )
    if created.returncode != 0:
        raise PolicyError(
            f"certutil failed to create the trust database (exit {created.returncode})"
        )

    imported = 0
    for cert_file in sorted(trust_dir.iterdir()):
        if cert_file.suffix not in {".pem", ".crt"} or not cert_file.is_file():
            continue
        blocks = _PEM_BLOCK_RE.findall(cert_file.read_bytes())
        for index, block in enumerate(blocks):
            fd, tmp_name = tempfile.mkstemp(dir=str(home_dir))
            tmp_path = Path(tmp_name)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, block + b"\n")
                os.close(fd)
                fd = -1
                nickname = f"cdpx-{cert_file.stem}-{index}"
                added = subprocess.run(
                    [
                        tool,
                        "-d",
                        db_arg,
                        "-A",
                        "-n",
                        nickname,
                        "-t",
                        "C,,",
                        "-i",
                        str(tmp_path),
                    ],
                    capture_output=True,
                )
                if added.returncode != 0:
                    raise PolicyError(
                        f"certutil failed to import {cert_file.name} "
                        f"block {index} (exit {added.returncode})"
                    )
                imported += 1
            finally:
                if fd >= 0:
                    os.close(fd)
                tmp_path.unlink(missing_ok=True)

    if imported == 0:
        raise PolicyError(f"trust store seeding imported zero certificates from {trust_dir}")
    return imported
