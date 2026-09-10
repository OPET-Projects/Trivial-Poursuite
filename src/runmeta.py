"""Provenance d'un run d'inférence.

Les runs sont répartis sur plusieurs postes aux matériels différents. Sans
ces métadonnées par ligne, la colonne response_time mélange des machines
incomparables et ne mesure plus rien.
"""

from __future__ import annotations

import platform
import re
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def model_slug(model_name: str) -> str:
    """Nom de modèle transformé en segment de chemin sûr."""
    slug = _UNSAFE.sub("_", model_name).strip("_")
    if slug in ("", ".", ".."):
        raise ValueError(
            f"Nom de modèle inexploitable comme segment de chemin: {model_name!r}"
        )
    return slug


def _cpu_brand() -> str:
    if sys.platform == "darwin":
        try:
            output = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if output.returncode == 0 and output.stdout.strip():
                return output.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.processor() or platform.machine()


def _runtime_version() -> str:
    try:
        import lmstudio

        return f"lmstudio-python/{getattr(lmstudio, '__version__', 'unknown')}"
    except ImportError:
        return "lmstudio-python/absent"


def host_info() -> dict[str, str]:
    return {
        "host": socket.gethostname(),
        "hardware": _cpu_brand(),
        "os_version": f"{platform.system()} {platform.release()}",
        "python_version": platform.python_version(),
        "runtime_version": _runtime_version(),
    }


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"
