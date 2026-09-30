### Task 3: Identité de run et provenance

**Files:**
- Create: `src/runmeta.py`
- Test: `tests/test_runmeta.py`

**Interfaces:**
- Consumes: rien.
- Produces: `model_slug(model_name: str) -> str`, `host_info() -> dict[str, str]` avec les clés `host`, `hardware`, `os_version`, `python_version`, `runtime_version`, `new_run_id() -> str`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_runmeta.py` :

```python
import re

from src.runmeta import host_info, model_slug, new_run_id


def test_model_slug_is_filesystem_safe():
    assert model_slug("google/gemma-4-12b-qat") == "google_gemma-4-12b-qat"


def test_model_slug_collapses_unsupported_characters():
    assert model_slug("prism-ml/Bonsai-27B GGUF:Q4_0") == "prism-ml_Bonsai-27B_GGUF_Q4_0"


def test_model_slug_is_stable():
    assert model_slug("a/b") == model_slug("a/b")


def test_host_info_exposes_required_keys():
    info = host_info()
    for key in ("host", "hardware", "os_version", "python_version", "runtime_version"):
        assert key in info
        assert isinstance(info[key], str)


def test_new_run_id_is_sortable_and_unique():
    first, second = new_run_id(), new_run_id()
    assert re.match(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$", first)
    assert first != second
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_runmeta.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.runmeta'`.

- [ ] **Step 3: Implémenter**

Créer `src/runmeta.py` :

```python
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
    return _UNSAFE.sub("_", model_name).strip("_")


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
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_runmeta.py -v`
Expected: PASS, 5 tests.


---

