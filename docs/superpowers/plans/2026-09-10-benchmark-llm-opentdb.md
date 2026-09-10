# Benchmark LLM sur OpenTDB — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transformer le squelette existant en pipeline complet bronze → silver → gold → dashboard, capable de benchmarker plusieurs modèles locaux sur l'intégralité du corpus OpenTDB avec trois variantes de prompt.

**Architecture:** Architecture médaillon à étages immuables. Chaque étage lit uniquement l'étage précédent et n'écrit que son propre artefact. L'inférence, coûteuse et définitive, est séparée du jugement, cheap et réexécutable. Les réponses sont stockées en parquet partitionné par modèle et variante de prompt, ce qui rend la reprise après interruption triviale et la mutualisation entre postes sans conflit.

**Tech Stack:** Python 3.10+, `requests`, `pandas`, `pyarrow`, `lmstudio` (SDK LM Studio), `rapidfuzz`, `tqdm`, `pytest`, `dbt-duckdb`, `duckdb`, `streamlit`, `altair`.

**Spec:** `docs/superpowers/specs/2026-09-10-benchmark-llm-opentdb-design.md`

## Global Constraints

- Python 3.10 minimum. Dépendances gérées par `requirements.txt` et un venv, **pas** `uv`, `poetry` ni `pipenv`.
- Structure de projet plate conservée : modules directement dans `src/`, `config.py` à la racine. Ne pas créer de package `src/trivia_bench/`.
- Aucun appel réseau ni appel LLM dans les tests. Fixtures enregistrées et doubles de test uniquement.
- Toute écriture de fichier de données passe par une écriture atomique : fichier temporaire dans le même répertoire puis `os.replace`.
- Clé fonctionnelle des résultats d'inférence et de jugement : le triplet `(question_id, model_slug, prompt_variant)`. Jamais `question_id` seul.
- Rythme OpenTDB : au moins 5,1 secondes entre deux requêtes sortantes, retries compris.
- `question_id` : `hashlib.sha256(payload).hexdigest()[:16]`, calculé une seule fois dans l'ingestion, jamais recalculé en aval.
- Les prompts envoyés aux modèles restent en anglais.
- Toute configuration variable d'un poste à l'autre passe par une variable d'environnement lue dans `config.py`, avec valeur par défaut.
- Travail directement sur `main`, **un commit par tâche**, effectué par l'agent qui réalise la tâche, une fois ses tests au vert. Messages en Conventional Commits, sans mention d'outil, sans ligne de coauteur, sans lien de session.
- `docs/superpowers/` n'est jamais versionné : spec et plan restent locaux.

## Structure des fichiers

| Fichier | Responsabilité |
| --- | --- |
| `config.py` | Chemins, constantes, paramètres lus depuis l'environnement |
| `src/io_utils.py` | Écritures atomiques CSV, parquet, JSON, JSONL |
| `src/runmeta.py` | Identité du poste, identifiant de run, slugification des noms de modèle |
| `src/opentdb_client.py` | Transport HTTP OpenTDB : rythme, retries, codes de réponse, décodage base64 |
| `src/ingest_opentdb.py` | Boucle de collecte par catégorie, dégradation du montant, checkpoint, bronze |
| `src/transform_silver.py` | Nettoyage, mélange seedé des options, colonnes dérivées |
| `src/prompts.py` | Registre versionné des trois variantes de prompt |
| `src/llm_client.py` | Wrapper LM Studio : appel, chronométrage, tokens, erreurs |
| `src/enrich_llm.py` | Inférence : reprise, écriture partitionnée par lots, provenance |
| `src/scoring.py` | Normalisation et primitives de comparaison |
| `src/judge.py` | Cascade de jugement, écriture des verdicts |
| `run_pipeline.py` | CLI d'orchestration |
| `dbt_project/` | Sources, staging, intermédiaire, marts, tests |
| `app/streamlit_app.py` | Dashboard sept pages |
| `tests/` | Suite pytest, fixtures OpenTDB enregistrées |

Le transport HTTP est extrait de `src/ingest_opentdb.py` vers `src/opentdb_client.py` : le fichier actuel mélange transport, boucle métier et persistance sur 339 lignes, ce qui empêche de tester la gestion des codes de réponse sans exécuter la collecte entière.

---

### Task 1: Configuration et socle de tests

**Files:**
- Modify: `config.py`
- Modify: `requirements.txt`
- Modify: `.env.example`
- Create: `tests/conftest.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: rien.
- Produces: `config.BRONZE_CSV`, `config.BRONZE_RESPONSES_DIR`, `config.INGEST_CHECKPOINT`, `config.SILVER_QUESTIONS`, `config.SILVER_ANSWERS_DIR`, `config.SILVER_JUDGMENTS_DIR`, `config.SILVER_RUNS_DIR`, `config.GOLD_DUCKDB` (tous `pathlib.Path`) ; `config.MODEL_NAME`, `config.JUDGE_MODEL_NAME` (`str`, depuis l'environnement) ; `config.RATE_LIMIT_SECONDS`, `config.AMOUNT_LADDER`, `config.FLUSH_EVERY`, `config.FUZZY_RATIO_THRESHOLD`, `config.JUDGMENT_VERSION`, `config.LLM_TEMPERATURE`, `config.LLM_MAX_TOKENS`, `config.LLM_MAX_ATTEMPTS`.

- [ ] **Step 1: Ajouter les dépendances**

Dans `requirements.txt`, ajouter sous les lignes existantes :

```
pytest>=8.3.0
duckdb>=1.1.0
dbt-duckdb>=1.9.0
streamlit>=1.40.0
altair>=5.4.0
```

- [ ] **Step 2: Écrire le test de configuration**

Créer `tests/conftest.py` :

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
```

Créer `tests/test_config.py` :

```python
import importlib

import config


def test_paths_are_under_data_dir():
    assert config.BRONZE_CSV.name == "questions_raw.csv"
    assert config.SILVER_QUESTIONS.suffix == ".parquet"
    assert config.SILVER_ANSWERS_DIR.name == "answers"
    assert config.SILVER_JUDGMENTS_DIR.name == "judgments"
    assert config.GOLD_DUCKDB.suffix == ".duckdb"


def test_model_name_comes_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "some/other-model")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.MODEL_NAME == "some/other-model"
    finally:
        monkeypatch.delenv("LLM_MODEL")
        importlib.reload(config)


def test_amount_ladder_is_descending_and_ends_at_one():
    assert config.AMOUNT_LADDER == [50, 25, 10, 5, 1]


def test_rate_limit_has_margin_over_api_minimum():
    assert config.RATE_LIMIT_SECONDS >= 5.1
```

- [ ] **Step 3: Lancer le test et vérifier l'échec**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL, `AttributeError: module 'config' has no attribute 'BRONZE_RESPONSES_DIR'` ou équivalent.

- [ ] **Step 4: Réécrire `config.py`**

```python
"""Paramètres globaux du pipeline Trivial Poursuite."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"
GOLD_DIR = DATA_DIR / "gold"

BRONZE_CSV = BRONZE_DIR / "questions_raw.csv"
BRONZE_RESPONSES_DIR = BRONZE_DIR / "_responses"
INGEST_CHECKPOINT = BRONZE_DIR / "ingest_checkpoint.json"

SILVER_QUESTIONS = SILVER_DIR / "questions.parquet"
SILVER_ANSWERS_DIR = SILVER_DIR / "answers"
SILVER_JUDGMENTS_DIR = SILVER_DIR / "judgments"
SILVER_RUNS_DIR = SILVER_DIR / "runs"

GOLD_DUCKDB = GOLD_DIR / "benchmark.duckdb"

# OpenTDB
OPENTDB_BASE = "https://opentdb.com"
OPENTDB_ENCODING = "base64"
AMOUNT_LADDER = [50, 25, 10, 5, 1]
RATE_LIMIT_SECONDS = 5.1
HTTP_TIMEOUT_SECONDS = 30
MAX_RETRIES = 8

# Échantillonnage, réservé aux smoke tests
SAMPLE_SIZE = 0
SAMPLE_SEED = 42

# LM Studio
MODEL_NAME = os.environ.get("LLM_MODEL", "google/gemma-4-12b-qat")
JUDGE_MODEL_NAME = os.environ.get("JUDGE_MODEL", MODEL_NAME)
LMSTUDIO_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "180"))
LLM_TEMPERATURE = 0.0
LLM_MAX_TOKENS = 64
LLM_MAX_ATTEMPTS = 3

# Écriture partitionnée
FLUSH_EVERY = 150

# Scoring
FUZZY_RATIO_THRESHOLD = 90
JUDGMENT_VERSION = "cascade_v1"
```

- [ ] **Step 5: Réécrire `.env.example`**

```
# Modèle interrogé, identifiant tel qu'affiché par LM Studio
LLM_MODEL=google/gemma-4-12b-qat

# Modèle utilisé pour arbitrer les réponses ambiguës. Par défaut, LLM_MODEL.
# JUDGE_MODEL=google/gemma-4-12b-qat

# Délai maximum d'un appel LLM, en secondes
# LLM_TIMEOUT_SECONDS=180
```

- [ ] **Step 6: Lancer les tests**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS, 4 tests.


---

### Task 2: Écritures atomiques

**Files:**
- Create: `src/io_utils.py`
- Test: `tests/test_io_utils.py`

**Interfaces:**
- Consumes: rien.
- Produces: `atomic_write_text(path: Path, content: str) -> None`, `atomic_write_json(path: Path, payload: dict) -> None`, `atomic_write_dataframe(df: pandas.DataFrame, path: Path, fmt: str) -> None` avec `fmt` valant `"csv"` ou `"parquet"`, `append_jsonl(path: Path, records: list[dict]) -> None`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_io_utils.py` :

```python
import json

import pandas as pd
import pytest

from src.io_utils import (
    append_jsonl,
    atomic_write_dataframe,
    atomic_write_json,
    atomic_write_text,
)


def test_atomic_write_text_creates_parent(tmp_path):
    target = tmp_path / "nested" / "file.txt"
    atomic_write_text(target, "bonjour")
    assert target.read_text(encoding="utf-8") == "bonjour"


def test_atomic_write_leaves_no_temp_file(tmp_path):
    target = tmp_path / "file.txt"
    atomic_write_text(target, "x")
    assert [p.name for p in tmp_path.iterdir()] == ["file.txt"]


def test_atomic_write_json_roundtrip(tmp_path):
    target = tmp_path / "meta.json"
    atomic_write_json(target, {"a": 1, "accent": "é"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "accent": "é"}


def test_atomic_write_dataframe_parquet(tmp_path):
    target = tmp_path / "df.parquet"
    atomic_write_dataframe(pd.DataFrame({"a": [1, 2]}), target, "parquet")
    assert len(pd.read_parquet(target)) == 2


def test_atomic_write_dataframe_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError):
        atomic_write_dataframe(pd.DataFrame({"a": [1]}), tmp_path / "x.txt", "txt")


def test_append_jsonl_accumulates(tmp_path):
    target = tmp_path / "log.jsonl"
    append_jsonl(target, [{"i": 1}])
    append_jsonl(target, [{"i": 2}, {"i": 3}])
    lines = target.read_text(encoding="utf-8").strip().split("\n")
    assert [json.loads(line)["i"] for line in lines] == [1, 2, 3]
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_io_utils.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.io_utils'`.

- [ ] **Step 3: Implémenter**

Créer `src/io_utils.py` :

```python
"""Écritures atomiques : on n'écrase jamais un fichier en place.

Un Ctrl-C au mauvais moment laisserait sinon un artefact tronqué qu'aucun
checkpoint ne signale.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

_FORMATS = {"csv", "parquet"}


def _replace_atomically(path: Path, write_to_temp) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        write_to_temp(temp_path)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def atomic_write_text(path: Path, content: str) -> None:
    _replace_atomically(path, lambda tmp: tmp.write_text(content, encoding="utf-8"))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))


def atomic_write_dataframe(df: pd.DataFrame, path: Path, fmt: str) -> None:
    if fmt not in _FORMATS:
        raise ValueError(f"Format non supporté: {fmt!r}. Attendu: {sorted(_FORMATS)}")

    def write(tmp: Path) -> None:
        if fmt == "csv":
            df.to_csv(tmp, index=False)
        else:
            df.to_parquet(tmp, index=False)

    _replace_atomically(path, write)


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_io_utils.py -v`
Expected: PASS, 6 tests.


---

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
    slug = _UNSAFE.sub("_", model_name).strip("_")
    if slug in ("", ".", ".."):
        raise ValueError(f"Nom de modèle inexploitable comme segment de chemin: {model_name!r}")
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
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_runmeta.py -v`
Expected: PASS, 5 tests.


---

### Task 4: Client HTTP OpenTDB

Extraction du transport hors de `src/ingest_opentdb.py`, avec le rythme respecté par les retries et le décodage base64.

**Files:**
- Create: `src/opentdb_client.py`
- Test: `tests/test_opentdb_client.py`

**Interfaces:**
- Consumes: `config`, `src.io_utils`.
- Produces:
  - `class ResponseCode` avec `SUCCESS = 0`, `NO_RESULTS = 1`, `INVALID_PARAMETER = 2`, `TOKEN_NOT_FOUND = 3`, `TOKEN_EMPTY = 4`, `RATE_LIMIT = 5`
  - `class RateLimiter(min_interval: float)` avec `wait() -> None`
  - `class OpenTDBClient(session=None, limiter=None)` avec `request_token() -> str`, `categories() -> list[dict]`, `category_count(category_id: int) -> int`, `global_verified_count() -> int`, `fetch(amount: int, category_id: int, token: str) -> tuple[int, list[dict], dict]` renvoyant `(response_code, questions_décodées, payload_brut)`
  - `decode_field(value: str) -> str`

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_opentdb_client.py` :

```python
import base64

import pytest

from src.opentdb_client import OpenTDBClient, RateLimiter, ResponseCode, decode_field


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Rejoue une liste de réponses et enregistre les paramètres reçus."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if not self.responses:
            raise AssertionError("Appel HTTP non prévu par le test")
        return self.responses.pop(0)


class FakeLimiter(RateLimiter):
    def __init__(self):
        super().__init__(min_interval=0.0)
        self.waits = 0

    def wait(self):
        self.waits += 1


def b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def question_payload(text="Who painted it?"):
    return {
        "category": b64("Art"),
        "type": b64("multiple"),
        "difficulty": b64("easy"),
        "question": b64(text),
        "correct_answer": b64("Leonardo da Vinci"),
        "incorrect_answers": [b64("Raphael"), b64("Titian"), b64("Donatello")],
    }


def test_decode_field_handles_base64():
    assert decode_field(b64("Don't forget π")) == "Don't forget π"


def test_fetch_decodes_every_field():
    session = FakeSession([FakeResponse({"response_code": 0, "results": [question_payload()]})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    code, questions, _ = client.fetch(amount=50, category_id=25, token="tok")
    assert code == ResponseCode.SUCCESS
    assert questions[0]["question"] == "Who painted it?"
    assert questions[0]["incorrect_answers"] == ["Raphael", "Titian", "Donatello"]


def test_fetch_sends_expected_parameters():
    session = FakeSession([FakeResponse({"response_code": 0, "results": []})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    client.fetch(amount=25, category_id=9, token="tok")
    _, params = session.calls[0]
    assert params == {"amount": 25, "category": 9, "token": "tok", "encode": "base64"}


def test_fetch_never_sends_offset_or_api_key():
    session = FakeSession([FakeResponse({"response_code": 0, "results": []})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    client.fetch(amount=50, category_id=9, token="tok")
    _, params = session.calls[0]
    assert "offset" not in params
    assert "apiKey" not in params


def test_limiter_is_consulted_before_every_call():
    limiter = FakeLimiter()
    session = FakeSession(
        [
            FakeResponse({"response_code": 0, "results": []}),
            FakeResponse({"response_code": 0, "results": []}),
        ]
    )
    client = OpenTDBClient(session=session, limiter=limiter)
    client.fetch(amount=50, category_id=9, token="tok")
    client.fetch(amount=50, category_id=10, token="tok")
    assert limiter.waits == 2


def test_retry_also_waits_between_attempts():
    limiter = FakeLimiter()
    session = FakeSession(
        [
            FakeResponse({}, status_code=500),
            FakeResponse({"response_code": 0, "results": []}),
        ]
    )
    client = OpenTDBClient(session=session, limiter=limiter)
    code, _, _ = client.fetch(amount=50, category_id=9, token="tok")
    assert code == ResponseCode.SUCCESS
    assert limiter.waits == 2


def test_request_token_raises_when_absent():
    session = FakeSession([FakeResponse({"response_code": 0})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    with pytest.raises(RuntimeError):
        client.request_token()


def test_category_count_reads_verified_total():
    payload = {
        "category_id": 25,
        "category_question_count": {
            "total_question_count": 137,
            "total_easy_question_count": 40,
        },
    }
    session = FakeSession([FakeResponse(payload)])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    assert client.category_count(25) == 137


def test_rate_limiter_enforces_interval(monkeypatch):
    now = {"t": 100.0}
    slept = []
    monkeypatch.setattr("src.opentdb_client.time.monotonic", lambda: now["t"])
    monkeypatch.setattr("src.opentdb_client.time.sleep", lambda s: slept.append(s))
    limiter = RateLimiter(min_interval=5.1)
    limiter.wait()
    limiter.wait()
    assert slept and pytest.approx(slept[-1], abs=0.01) == 5.1
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_opentdb_client.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.opentdb_client'`.

- [ ] **Step 3: Implémenter**

Créer `src/opentdb_client.py` :

```python
"""Transport HTTP OpenTDB.

Séparé de la boucle de collecte pour que la gestion des codes de réponse
soit testable sans exécuter un scrape complet.
"""

from __future__ import annotations

import base64
import binascii
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config


class ResponseCode:
    SUCCESS = 0
    NO_RESULTS = 1
    INVALID_PARAMETER = 2
    TOKEN_NOT_FOUND = 3
    TOKEN_EMPTY = 4
    RATE_LIMIT = 5


class RateLimiter:
    """Garantit un intervalle minimum entre deux requêtes sortantes.

    Fondé sur une échéance et non sur un sleep fixe : les retries HTTP
    passent par le même point de contrôle et ne peuvent donc pas déclencher
    un code 5 en rafale.
    """

    def __init__(self, min_interval: float = config.RATE_LIMIT_SECONDS) -> None:
        self.min_interval = min_interval
        self._next_allowed = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if now < self._next_allowed:
            time.sleep(self._next_allowed - now)
            now = time.monotonic()
        self._next_allowed = now + self.min_interval


def decode_field(value: Any) -> str:
    """Décode un champ base64 renvoyé par l'API."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return value


def _decode_question(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "category": decode_field(item.get("category")),
        "type": decode_field(item.get("type")),
        "difficulty": decode_field(item.get("difficulty")),
        "question": decode_field(item.get("question")),
        "correct_answer": decode_field(item.get("correct_answer")),
        "incorrect_answers": [decode_field(a) for a in item.get("incorrect_answers") or []],
    }


class OpenTDBClient:
    def __init__(self, session: Any = None, limiter: RateLimiter | None = None) -> None:
        self.session = session or requests.Session()
        if hasattr(self.session, "headers"):
            self.session.headers.update({"User-Agent": "trivial-poursuite-benchmark/2.0"})
        self.limiter = limiter or RateLimiter()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{config.OPENTDB_BASE}/{path}"
        last_error: Exception | None = None
        for _ in range(config.MAX_RETRIES):
            self.limiter.wait()
            try:
                response = self.session.get(url, params=params, timeout=config.HTTP_TIMEOUT_SECONDS)
                if response.status_code == 429:
                    last_error = RuntimeError("HTTP 429")
                    continue
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
        raise RuntimeError(f"Échec HTTP après {config.MAX_RETRIES} tentatives: {url}") from last_error

    def request_token(self) -> str:
        payload = self._get("api_token.php", {"command": "request"})
        token = payload.get("token")
        if not token:
            raise RuntimeError(f"Token OpenTDB absent de la réponse: {payload}")
        return token

    def categories(self) -> list[dict[str, Any]]:
        payload = self._get("api_category.php")
        categories = payload.get("trivia_categories") or []
        if not categories:
            raise RuntimeError(f"Liste de catégories vide: {payload}")
        return categories

    def category_count(self, category_id: int) -> int:
        payload = self._get("api_count.php", {"category": category_id})
        counts = payload.get("category_question_count") or {}
        return int(counts.get("total_question_count") or 0)

    def global_verified_count(self) -> int:
        payload = self._get("api_count_global.php")
        overall = payload.get("overall") or {}
        return int(overall.get("total_num_of_verified_questions") or 0)

    def fetch(self, amount: int, category_id: int, token: str) -> tuple[int, list[dict], dict]:
        params = {
            "amount": amount,
            "category": category_id,
            "token": token,
            "encode": config.OPENTDB_ENCODING,
        }
        payload = self._get("api.php", params)
        code = int(payload.get("response_code", -1))
        questions = [_decode_question(item) for item in payload.get("results") or []]
        return code, questions, payload
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_opentdb_client.py -v`
Expected: PASS, 9 tests.


---

### Task 5: Collecte intégrale vers la couche bronze

Corrige la perte de la queue de chaque catégorie, fige `question_id` au plus tôt, conserve les payloads bruts.

**Files:**
- Modify: `src/ingest_opentdb.py` (réécriture complète)
- Test: `tests/test_ingest_opentdb.py`

**Interfaces:**
- Consumes: `src.opentdb_client.OpenTDBClient`, `src.io_utils`, `config`.
- Produces: `make_question_id(question: str, correct_answer: str) -> str`, `fetch_category(client, token: str, category_id: int, seen: set[str], on_payload=None) -> tuple[list[dict], str]` renvoyant les lignes bronze et le token courant, `run_ingest(force: bool = False, client=None) -> Path`. `on_payload` est un callable optionnel appelé avec chaque payload d'API pour archivage.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_ingest_opentdb.py` :

```python
import pandas as pd
import pytest

from src.ingest_opentdb import fetch_category, make_question_id, run_ingest
from src.opentdb_client import ResponseCode


class ScriptedClient:
    """Client OpenTDB scripté : chaque entrée décrit la réponse à un fetch."""

    def __init__(self, script, categories=None, counts=None, duplicate_batches=False):
        self.script = list(script)
        self.duplicate_batches = duplicate_batches
        self.fetch_calls = []
        self.tokens_issued = 0
        self._categories = categories or [{"id": 9, "name": "General Knowledge"}]
        self._counts = counts or {9: 137}

    def request_token(self):
        self.tokens_issued += 1
        return f"token-{self.tokens_issued}"

    def categories(self):
        return self._categories

    def category_count(self, category_id):
        return self._counts.get(category_id, 0)

    def global_verified_count(self):
        return sum(self._counts.values())

    def fetch(self, amount, category_id, token):
        self.fetch_calls.append({"amount": amount, "category_id": category_id, "token": token})
        if not self.script:
            raise AssertionError("fetch non prévu par le test")
        code, count = self.script.pop(0)
        questions = [
            {
                "category": "General Knowledge",
                "type": "multiple",
                "difficulty": "easy",
                "question": f"Q{i}" if self.duplicate_batches else f"Q{len(self.fetch_calls)}-{i}",
                "correct_answer": "A",
                "incorrect_answers": ["B", "C", "D"],
            }
            for i in range(count)
        ]
        return code, questions, {"response_code": code}


def test_question_id_is_stable_across_whitespace_and_case():
    first = make_question_id("Who  painted  it?", "Leonardo da Vinci")
    second = make_question_id("who painted it?", "leonardo DA vinci")
    assert first == second
    assert len(first) == 16


def test_question_id_differs_for_different_answers():
    assert make_question_id("Q", "A") != make_question_id("Q", "B")


def test_ladder_degrades_before_giving_up():
    client = ScriptedClient(
        script=[
            (ResponseCode.SUCCESS, 50),
            (ResponseCode.SUCCESS, 50),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.SUCCESS, 25),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.NO_RESULTS, 0),
        ]
    )
    rows, _ = fetch_category(client, "token-1", 9, set())
    assert len(rows) == 125
    assert [call["amount"] for call in client.fetch_calls] == [50, 50, 50, 25, 25, 10, 5, 1]


def test_category_of_137_returns_137_rows():
    client = ScriptedClient(
        script=[
            (ResponseCode.SUCCESS, 50),
            (ResponseCode.SUCCESS, 50),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.SUCCESS, 25),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.SUCCESS, 10),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.SUCCESS, 1),
            (ResponseCode.SUCCESS, 1),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.NO_RESULTS, 0),
        ]
    )
    rows, _ = fetch_category(client, "token-1", 9, set())
    assert len(rows) == 137


def test_token_empty_stops_the_category_without_reset():
    client = ScriptedClient(script=[(ResponseCode.SUCCESS, 50), (ResponseCode.TOKEN_EMPTY, 0)])
    rows, token = fetch_category(client, "token-1", 9, set())
    assert len(rows) == 50
    assert token == "token-1"
    assert client.tokens_issued == 0


def test_token_not_found_requests_a_new_token_and_continues():
    client = ScriptedClient(
        script=[
            (ResponseCode.TOKEN_NOT_FOUND, 0),
            (ResponseCode.SUCCESS, 10),
            (ResponseCode.TOKEN_EMPTY, 0),
        ]
    )
    rows, token = fetch_category(client, "expired", 9, set())
    assert client.tokens_issued == 1
    assert token == "token-1"
    assert len(rows) == 10


def test_rate_limit_retries_same_amount():
    client = ScriptedClient(
        script=[
            (ResponseCode.RATE_LIMIT, 0),
            (ResponseCode.SUCCESS, 5),
            (ResponseCode.TOKEN_EMPTY, 0),
        ]
    )
    rows, _ = fetch_category(client, "token-1", 9, set())
    assert [call["amount"] for call in client.fetch_calls] == [50, 50, 50]
    assert len(rows) == 5


def test_duplicates_are_skipped_via_seen_set():
    client = ScriptedClient(
        script=[(ResponseCode.SUCCESS, 3), (ResponseCode.SUCCESS, 3), (ResponseCode.TOKEN_EMPTY, 0)],
        duplicate_batches=True,
    )
    seen = set()
    rows, _ = fetch_category(client, "token-1", 9, seen)
    ids = {row["question_id"] for row in rows}
    assert len(rows) == 3, "le second lot est identique au premier, il doit être écarté"
    assert len(ids) == 3
    assert seen == ids


def test_bronze_rows_carry_the_expected_columns():
    client = ScriptedClient(script=[(ResponseCode.SUCCESS, 1), (ResponseCode.TOKEN_EMPTY, 0)])
    rows, _ = fetch_category(client, "token-1", 9, set())
    expected = {
        "question_id",
        "category",
        "type",
        "difficulty",
        "question",
        "correct_answer",
        "incorrect_answers",
        "fetched_at",
        "batch_id",
    }
    assert set(rows[0]) == expected


def test_run_ingest_writes_csv_and_checkpoint(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "BRONZE_CSV", tmp_path / "questions_raw.csv")
    monkeypatch.setattr(config, "BRONZE_RESPONSES_DIR", tmp_path / "_responses")
    monkeypatch.setattr(config, "INGEST_CHECKPOINT", tmp_path / "checkpoint.json")
    client = ScriptedClient(script=[(ResponseCode.SUCCESS, 4), (ResponseCode.TOKEN_EMPTY, 0)])
    run_ingest(client=client)
    frame = pd.read_csv(tmp_path / "questions_raw.csv")
    assert len(frame) == 4
    assert (tmp_path / "checkpoint.json").exists()
    assert any((tmp_path / "_responses").iterdir())
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_ingest_opentdb.py -v`
Expected: FAIL, `ImportError: cannot import name 'fetch_category'`.

- [ ] **Step 3: Réécrire `src/ingest_opentdb.py`**

```python
"""Collecte intégrale OpenTDB vers la couche bronze.

Piège central de l'API : le code 1 est renvoyé sans aucune question quand la
catégorie contient moins de questions non servies que le nombre demandé.
Demander systématiquement 50 perd donc la queue de chaque catégorie. On
dégrade le montant demandé avant de conclure à l'épuisement.
"""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src.io_utils import append_jsonl, atomic_write_dataframe, atomic_write_json
from src.opentdb_client import OpenTDBClient, ResponseCode

_SPACES = re.compile(r"\s+")

BRONZE_COLUMNS = [
    "question_id",
    "category",
    "type",
    "difficulty",
    "question",
    "correct_answer",
    "incorrect_answers",
    "fetched_at",
    "batch_id",
]


def make_question_id(question: str, correct_answer: str) -> str:
    """Identifiant stable, calculé une seule fois et jamais recalculé.

    La normalisation reste minimale — casse et espaces — pour que l'identifiant
    ne dépende d'aucune décision de nettoyage ultérieure. Sinon un changement
    dans le silver rendrait orphelines des heures d'inférence.
    """
    normalized_q = _SPACES.sub(" ", question.strip().casefold())
    normalized_a = _SPACES.sub(" ", correct_answer.strip().casefold())
    payload = f"{normalized_q}␟{normalized_a}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _to_row(item: dict[str, Any], batch_id: str) -> dict[str, Any]:
    import json

    return {
        "question_id": make_question_id(item["question"], item["correct_answer"]),
        "category": item["category"],
        "type": item["type"],
        "difficulty": item["difficulty"],
        "question": item["question"],
        "correct_answer": item["correct_answer"],
        "incorrect_answers": json.dumps(item["incorrect_answers"], ensure_ascii=False),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "batch_id": batch_id,
    }


def fetch_category(
    client: Any,
    token: str,
    category_id: int,
    seen: set[str],
    on_payload=None,
) -> tuple[list[dict[str, Any]], str]:
    """Vide une catégorie, en dégradant le montant demandé avant d'abandonner."""
    rows: list[dict[str, Any]] = []
    ladder_index = 0
    call_index = 0

    while ladder_index < len(config.AMOUNT_LADDER):
        amount = config.AMOUNT_LADDER[ladder_index]
        code, questions, payload = client.fetch(amount=amount, category_id=category_id, token=token)
        call_index += 1
        batch_id = f"cat{category_id}-{call_index:04d}"

        if on_payload is not None:
            on_payload({"batch_id": batch_id, "category_id": category_id, "payload": payload})

        if code == ResponseCode.TOKEN_NOT_FOUND:
            token = client.request_token()
            continue

        if code == ResponseCode.RATE_LIMIT:
            continue

        if code in (ResponseCode.NO_RESULTS, ResponseCode.TOKEN_EMPTY):
            # L'API renvoie le code 4, et non le code 1, quand le reste non servi
            # est inférieur au montant demandé. Vérifié sur la catégorie 16.
            ladder_index += 1
            continue

        if code != ResponseCode.SUCCESS:
            ladder_index += 1
            continue

        for item in questions:
            row = _to_row(item, batch_id)
            if row["question_id"] in seen:
                continue
            seen.add(row["question_id"])
            rows.append(row)

    return rows, token


def run_ingest(*, force: bool = False, client: Any = None) -> Path:
    client = client or OpenTDBClient()
    checkpoint_path = config.INGEST_CHECKPOINT

    existing: list[dict[str, Any]] = []
    if not force and config.BRONZE_CSV.exists():
        existing = pd.read_csv(config.BRONZE_CSV).to_dict(orient="records")

    seen = {str(row["question_id"]) for row in existing}
    rows = list(existing)

    expected = client.global_verified_count()
    categories = client.categories()
    token = client.request_token()

    def record_payload(entry: dict[str, Any]) -> None:
        append_jsonl(config.BRONZE_RESPONSES_DIR / f"cat{entry['category_id']}.jsonl", [entry])

    progress = tqdm(total=expected or None, initial=len(rows), unit="q", desc="OpenTDB")
    try:
        for category in categories:
            category_id = int(category["id"])
            progress.set_postfix(cat=str(category.get("name", category_id))[:24])
            before = len(rows)
            new_rows, token = fetch_category(client, token, category_id, seen, record_payload)
            rows.extend(new_rows)
            progress.update(len(rows) - before)

            atomic_write_dataframe(pd.DataFrame(rows, columns=BRONZE_COLUMNS), config.BRONZE_CSV, "csv")
            atomic_write_json(
                checkpoint_path,
                {
                    "token": token,
                    "n_questions": len(rows),
                    "last_category_id": category_id,
                    "expected_verified": expected,
                    "status": "in_progress",
                },
            )
    finally:
        progress.close()

    atomic_write_dataframe(pd.DataFrame(rows, columns=BRONZE_COLUMNS), config.BRONZE_CSV, "csv")
    atomic_write_json(
        checkpoint_path,
        {
            "token": token,
            "n_questions": len(rows),
            "expected_verified": expected,
            "status": "complete",
        },
    )
    print(f"[ingest] {len(rows)} questions uniques sur {expected} attendues → {config.BRONZE_CSV}")
    return config.BRONZE_CSV


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Collecte intégrale OpenTDB vers la couche bronze.")
    parser.add_argument("--force", action="store_true", help="Ignore le bronze existant.")
    args = parser.parse_args()
    run_ingest(force=args.force)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_ingest_opentdb.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Vérifier le comportement réel sur une catégorie**

Run: `python -c "
from src.opentdb_client import OpenTDBClient
from src.ingest_opentdb import fetch_category
c = OpenTDBClient()
print('attendu', c.category_count(16))
rows, _ = fetch_category(c, c.request_token(), 16, set())
print('obtenu', len(rows))
"`
Expected: les deux nombres sont égaux. La catégorie 16 (Board Games) est petite, ce contrôle prend moins de deux minutes.


---

### Task 6: Nettoyage silver et mélange seedé des options

**Files:**
- Modify: `src/transform_silver.py` (réécriture complète)
- Test: `tests/test_transform_silver.py`

**Interfaces:**
- Consumes: `config`, `src.io_utils`, sortie de la Task 5.
- Produces: `split_category(label: str) -> tuple[str, str]`, `shuffled_choices(question_id: str, correct: str, incorrect: list[str]) -> list[str]`, `clean_questions(df: pandas.DataFrame) -> pandas.DataFrame`, `run_transform() -> Path`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_transform_silver.py` :

```python
import json

import pandas as pd

from src.transform_silver import clean_questions, shuffled_choices, split_category


def bronze_frame(**overrides):
    row = {
        "question_id": "abc123def4567890",
        "category": "Entertainment: Film",
        "type": "multiple",
        "difficulty": "easy",
        "question": "Who directed &quot;Jaws&quot;?",
        "correct_answer": "Steven Spielberg",
        "incorrect_answers": json.dumps(["George Lucas", "Ridley Scott", "Brian De Palma"]),
        "fetched_at": "2026-09-10T10:00:00+00:00",
        "batch_id": "cat11-0001",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_split_category_separates_group_and_name():
    assert split_category("Entertainment: Film") == ("Entertainment", "Film")


def test_split_category_without_separator():
    assert split_category("Geography") == ("General", "Geography")


def test_shuffle_is_deterministic_for_a_given_question_id():
    first = shuffled_choices("id-1", "A", ["B", "C", "D"])
    second = shuffled_choices("id-1", "A", ["B", "C", "D"])
    assert first == second


def test_shuffle_differs_between_question_ids():
    orders = {tuple(shuffled_choices(f"id-{i}", "A", ["B", "C", "D"])) for i in range(40)}
    assert len(orders) > 1


def test_shuffle_keeps_every_choice_exactly_once():
    choices = shuffled_choices("id-1", "A", ["B", "C", "D"])
    assert sorted(choices) == ["A", "B", "C", "D"]


def test_boolean_options_are_not_always_false_first():
    positions = {
        shuffled_choices(f"id-{i}", "True", ["False"]).index("False") for i in range(40)
    }
    assert positions == {0, 1}


def test_html_entities_are_decoded():
    cleaned = clean_questions(bronze_frame())
    assert cleaned.loc[0, "question"] == 'Who directed "Jaws"?'


def test_double_encoded_entities_are_decoded():
    cleaned = clean_questions(bronze_frame(question="Rock &amp;amp; Roll"))
    assert cleaned.loc[0, "question"] == "Rock & Roll"


def test_question_id_is_carried_over_untouched():
    cleaned = clean_questions(bronze_frame())
    assert cleaned.loc[0, "question_id"] == "abc123def4567890"


def test_correct_answer_position_matches_choices():
    cleaned = clean_questions(bronze_frame())
    position = cleaned.loc[0, "correct_answer_position"]
    assert cleaned.loc[0, "choices"][position] == "Steven Spielberg"


def test_numeric_answer_is_flagged():
    cleaned = clean_questions(bronze_frame(correct_answer="1789"))
    assert bool(cleaned.loc[0, "answer_is_numeric"]) is True


def test_non_numeric_answer_is_not_flagged():
    cleaned = clean_questions(bronze_frame())
    assert bool(cleaned.loc[0, "answer_is_numeric"]) is False


def test_rows_without_question_are_dropped():
    frame = pd.concat([bronze_frame(), bronze_frame(question="", question_id="zzz")], ignore_index=True)
    assert len(clean_questions(frame)) == 1


def test_duplicate_question_ids_are_dropped():
    frame = pd.concat([bronze_frame(), bronze_frame()], ignore_index=True)
    assert len(clean_questions(frame)) == 1
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_transform_silver.py -v`
Expected: FAIL, `ImportError: cannot import name 'shuffled_choices'`.

- [ ] **Step 3: Réécrire `src/transform_silver.py`**

```python
"""Nettoyage bronze → silver/questions.parquet.

L'ordre des options est mélangé avec un générateur seedé par question_id.
Trié alphabétiquement, l'ordre plaçait toujours False en première position sur
les booléens et ordonnait les réponses numériques par magnitude : le biais de
position bien documenté des modèles devenait alors corrélé au contenu, donc non
uniforme selon la catégorie.
"""

from __future__ import annotations

import ast
import html
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src.io_utils import atomic_write_dataframe

_SPACES = re.compile(r"\s+")
_NUMERIC = re.compile(r"^(?=.*\d)[\d\s.,%/+-]+$")

SILVER_COLUMNS = [
    "question_id",
    "category",
    "category_group",
    "category_name",
    "type",
    "difficulty",
    "question",
    "correct_answer",
    "incorrect_answers",
    "choices",
    "correct_answer_position",
    "n_choices",
    "correct_answer_norm",
    "answer_is_numeric",
    "question_len",
    "answer_len",
    "cleaned_at",
]


def unescape_text(value: object) -> str:
    """Déplie les entités HTML, y compris doublement encodées."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value)
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return _SPACES.sub(" ", text).strip()


def split_category(label: str) -> tuple[str, str]:
    if ":" in label:
        group, name = label.split(":", 1)
        return group.strip(), name.strip()
    return "General", label.strip()


def parse_incorrect(value: object) -> list[str]:
    if isinstance(value, list):
        return [unescape_text(item) for item in value]
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = ast.literal_eval(text)
    if not isinstance(parsed, list):
        return [unescape_text(parsed)]
    return [unescape_text(item) for item in parsed]


def shuffled_choices(question_id: str, correct: str, incorrect: list[str]) -> list[str]:
    """Les doublons exacts sont écartés : OpenTDB contient des lignes où une
    mauvaise réponse reprend mot pour mot la bonne, ce qui rendrait
    `correct_answer_position` ambigu."""
    choices = [correct]
    for answer in incorrect:
        if answer not in choices:
            choices.append(answer)
    random.Random(question_id).shuffle(choices)
    return choices


def normalize_answer(value: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^\w\s]", " ", text.casefold())
    return _SPACES.sub(" ", text).strip()


def clean_questions(df: pd.DataFrame) -> pd.DataFrame:
    records = []
    now = datetime.now(timezone.utc).isoformat()

    for row in df.to_dict(orient="records"):
        question = unescape_text(row.get("question"))
        correct = unescape_text(row.get("correct_answer"))
        if not question or not correct:
            continue

        category = unescape_text(row.get("category"))
        group, name = split_category(category)
        incorrect = parse_incorrect(row.get("incorrect_answers"))
        question_id = str(row["question_id"])
        choices = shuffled_choices(question_id, correct, incorrect)

        records.append(
            {
                "question_id": question_id,
                "category": category,
                "category_group": group,
                "category_name": name,
                "type": unescape_text(row.get("type")).lower(),
                "difficulty": unescape_text(row.get("difficulty")).lower(),
                "question": question,
                "correct_answer": correct,
                "incorrect_answers": incorrect,
                "choices": choices,
                "correct_answer_position": choices.index(correct),
                "n_choices": len(choices),
                "correct_answer_norm": normalize_answer(correct),
                "answer_is_numeric": bool(_NUMERIC.match(correct.strip())),
                "question_len": len(question),
                "answer_len": len(correct),
                "cleaned_at": now,
            }
        )

    cleaned = pd.DataFrame(records, columns=SILVER_COLUMNS)
    return cleaned.drop_duplicates(subset=["question_id"]).reset_index(drop=True)


def run_transform() -> Path:
    if not config.BRONZE_CSV.exists():
        raise FileNotFoundError(f"Couche bronze introuvable: {config.BRONZE_CSV}")
    raw = pd.read_csv(config.BRONZE_CSV)
    cleaned = clean_questions(raw)
    atomic_write_dataframe(cleaned, config.SILVER_QUESTIONS, "parquet")
    print(f"[silver] {len(raw)} brutes → {len(cleaned)} propres → {config.SILVER_QUESTIONS}")
    return config.SILVER_QUESTIONS


def main() -> None:
    run_transform()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_transform_silver.py -v`
Expected: PASS, 14 tests.


---

### Task 7: Registre des variantes de prompt

**Files:**
- Create: `src/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: rien.
- Produces: `PROMPT_VARIANTS: dict[str, PromptVariant]` avec les clés `p1_constrained_mcq`, `p2_open_minimal`, `p3_open_guided` ; `class PromptVariant` exposant `variant_id: str`, `system_prompt: str`, `build_user_prompt(question_row: Mapping) -> str`, `prompt_hash: str`, `mode: str` (`"constrained"` ou `"open"`) ; `get_variant(variant_id: str) -> PromptVariant`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_prompts.py` :

```python
import pytest

from src.prompts import PROMPT_VARIANTS, get_variant

MULTIPLE = {
    "question": "Who painted the Mona Lisa?",
    "category": "Art",
    "difficulty": "easy",
    "type": "multiple",
    "choices": ["Raphael", "Leonardo da Vinci", "Titian", "Donatello"],
}

BOOLEAN = {
    "question": "The Earth is flat.",
    "category": "Science & Nature",
    "difficulty": "easy",
    "type": "boolean",
    "choices": ["True", "False"],
}


def test_three_variants_are_registered():
    assert set(PROMPT_VARIANTS) == {"p1_constrained_mcq", "p2_open_minimal", "p3_open_guided"}


def test_constrained_variant_lists_the_choices_in_order():
    prompt = get_variant("p1_constrained_mcq").build_user_prompt(MULTIPLE)
    assert prompt.index("Raphael") < prompt.index("Leonardo da Vinci")
    assert "Titian" in prompt


def test_open_variants_never_leak_the_choices():
    for variant_id in ("p2_open_minimal", "p3_open_guided"):
        prompt = get_variant(variant_id).build_user_prompt(MULTIPLE)
        assert "Titian" not in prompt
        assert "Leonardo da Vinci" not in prompt


def test_every_variant_includes_the_question():
    for variant in PROMPT_VARIANTS.values():
        assert "Who painted the Mona Lisa?" in variant.build_user_prompt(MULTIPLE)


def test_boolean_questions_get_true_false_instruction_in_open_modes():
    for variant_id in ("p2_open_minimal", "p3_open_guided"):
        prompt = get_variant(variant_id).build_user_prompt(BOOLEAN)
        assert "True" in prompt and "False" in prompt


def test_modes_are_declared():
    assert get_variant("p1_constrained_mcq").mode == "constrained"
    assert get_variant("p2_open_minimal").mode == "open"
    assert get_variant("p3_open_guided").mode == "open"


def test_prompt_hash_is_stable_and_distinct():
    hashes = {v.variant_id: v.prompt_hash for v in PROMPT_VARIANTS.values()}
    assert len(set(hashes.values())) == 3
    assert get_variant("p1_constrained_mcq").prompt_hash == hashes["p1_constrained_mcq"]


def test_unknown_variant_raises():
    with pytest.raises(KeyError):
        get_variant("nope")
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.prompts'`.

- [ ] **Step 3: Implémenter**

Créer `src/prompts.py` :

```python
"""Registre versionné des variantes de prompt.

L'écart p1 / p3 mesure la différence entre reconnaître une réponse parmi
des options et la restituer librement. L'écart p2 / p3 isole l'apport de
l'ingénierie de prompt à mode d'interrogation constant.

Les prompts restent en anglais, comme le corpus OpenTDB.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Mapping

_CONSTRAINED_SYSTEM = (
    "You are a trivia answering engine. "
    "Reply with the answer only. "
    "No explanation, no extra punctuation, no markdown, no quotes. "
    "Copy one of the given options verbatim."
)

_MINIMAL_SYSTEM = "You are a trivia answering engine. Answer with the answer only."

_GUIDED_SYSTEM = (
    "You are a trivia answering engine. "
    "Reply with the shortest exact answer and nothing else. "
    "No explanation, no reasoning, no full sentence, no leading article, "
    "no trailing period, no markdown, no quotes. "
    "For a person, give the full name. For a date, give the year only unless "
    "the question asks otherwise. For a true/false statement, reply True or False."
)


def _constrained_user(row: Mapping) -> str:
    options = "\n".join(f"- {choice}" for choice in row["choices"])
    return (
        f"Category: {row['category']}\n"
        f"Difficulty: {row['difficulty']}\n"
        f"Question: {row['question']}\n"
        f"Options:\n{options}\n"
        "Copy one option verbatim.\n"
        "Answer:"
    )


def _minimal_user(row: Mapping) -> str:
    suffix = "\nAnswer True or False." if row["type"] == "boolean" else ""
    return f"Question: {row['question']}{suffix}\nAnswer:"


def _guided_user(row: Mapping) -> str:
    if row["type"] == "boolean":
        instruction = "Reply with exactly one word: True or False."
    else:
        instruction = "Reply with the exact answer only, no sentence, no explanation."
    return (
        f"Category: {row['category']}\n"
        f"Difficulty: {row['difficulty']}\n"
        f"Question: {row['question']}\n"
        f"{instruction}\n"
        "Answer:"
    )


@dataclass(frozen=True)
class PromptVariant:
    variant_id: str
    mode: str
    system_prompt: str
    _builder: Callable[[Mapping], str]

    def build_user_prompt(self, question_row: Mapping) -> str:
        return self._builder(question_row)

    @property
    def prompt_hash(self) -> str:
        """Les deux types de question sont rendus : les variantes ouvertes
        produisent une consigne différente pour les booléens, et une dérive de
        ce texte doit changer l'empreinte."""
        probe_multiple = {
            "question": "<probe>",
            "category": "<category>",
            "difficulty": "<difficulty>",
            "type": "multiple",
            "choices": ["<a>", "<b>"],
        }
        probe_boolean = {**probe_multiple, "type": "boolean", "choices": ["True", "False"]}
        payload = "␟".join(
            [self.system_prompt, self._builder(probe_multiple), self._builder(probe_boolean)]
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:12]


PROMPT_VARIANTS: dict[str, PromptVariant] = {
    "p1_constrained_mcq": PromptVariant(
        "p1_constrained_mcq", "constrained", _CONSTRAINED_SYSTEM, _constrained_user
    ),
    "p2_open_minimal": PromptVariant(
        "p2_open_minimal", "open", _MINIMAL_SYSTEM, _minimal_user
    ),
    "p3_open_guided": PromptVariant(
        "p3_open_guided", "open", _GUIDED_SYSTEM, _guided_user
    ),
}


def get_variant(variant_id: str) -> PromptVariant:
    if variant_id not in PROMPT_VARIANTS:
        raise KeyError(f"Variante inconnue: {variant_id!r}. Connues: {sorted(PROMPT_VARIANTS)}")
    return PROMPT_VARIANTS[variant_id]
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: PASS, 8 tests.


---

### Task 8: Wrapper LM Studio

**Files:**
- Create: `src/llm_client.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: `config`, SDK `lmstudio`.
- Produces: `@dataclass LLMResult` avec `text: str`, `raw_text: str`, `response_time: float`, `prompt_tokens: int | None`, `completion_tokens: int | None`, `finish_reason: str`, `status: str`, `error: str`, `attempt: int` ; `class LLMClient(model_name: str, backend=None)` avec `complete(system_prompt: str, user_prompt: str) -> LLMResult` ; `clean_answer(text: str) -> str`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_llm_client.py` :

```python
import pytest

from src.llm_client import LLMClient, clean_answer


class FakeBackend:
    """Double de test du backend LM Studio."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def respond(self, system_prompt, user_prompt):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_clean_answer_strips_quotes_and_trailing_period():
    assert clean_answer('  "Leonardo da Vinci."  ') == "Leonardo da Vinci"


def test_clean_answer_keeps_internal_punctuation():
    assert clean_answer("Rock & Roll, Part 2") == "Rock & Roll, Part 2"


def test_clean_answer_takes_first_line_only():
    assert clean_answer("Paris\nBecause it is the capital.") == "Paris"


def test_clean_answer_strips_answer_prefix():
    assert clean_answer("Answer: Paris") == "Paris"


def test_complete_returns_text_and_timing():
    backend = FakeBackend([{"text": "Paris", "prompt_tokens": 30, "completion_tokens": 2,
                            "finish_reason": "stop"}])
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.text == "Paris"
    assert result.status == "ok"
    assert result.prompt_tokens == 30
    assert result.completion_tokens == 2
    assert result.response_time >= 0


def test_complete_retries_on_transient_error():
    backend = FakeBackend([RuntimeError("connection reset"), {"text": "Paris"}])
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.status == "ok"
    assert result.attempt == 2
    assert backend.calls == 2


def test_complete_gives_up_after_max_attempts():
    backend = FakeBackend([RuntimeError("boom")] * 3)
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.status == "error"
    assert result.text == ""
    assert "boom" in result.error
    assert backend.calls == 3


def test_missing_token_counts_become_none():
    backend = FakeBackend([{"text": "Paris"}])
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.llm_client'`.

- [ ] **Step 3: Implémenter**

Créer `src/llm_client.py` :

```python
"""Appel au runtime local, chronométré et instrumenté.

Le backend est injectable : les tests n'appellent jamais LM Studio, et
remplacer le runtime ne touche que LMStudioBackend.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

_PREFIX = re.compile(r"^(answer|réponse)\s*[:\-]\s*", re.IGNORECASE)
_WRAPPING_QUOTES = re.compile(r'^["\'`«»\s]+|["\'`«»\s]+$')


def clean_answer(text: str) -> str:
    """Ramène une sortie de modèle à sa réponse nue."""
    if not text:
        return ""
    first_line = text.strip().split("\n")[0]
    first_line = _PREFIX.sub("", first_line.strip())
    first_line = _WRAPPING_QUOTES.sub("", first_line)
    if first_line.endswith(".") and not first_line.endswith(".."):
        first_line = first_line[:-1]
    return first_line.strip()


@dataclass
class LLMResult:
    text: str
    raw_text: str
    response_time: float
    prompt_tokens: int | None
    completion_tokens: int | None
    finish_reason: str
    status: str
    error: str
    attempt: int


class LMStudioBackend:
    """Adaptateur du SDK lmstudio.

    Les noms de champs de statistiques varient selon la version du SDK : on
    les lit défensivement plutôt que de supposer un schéma.
    """

    def __init__(self, model_name: str) -> None:
        import lmstudio as lms

        lms.set_sync_api_timeout(config.LMSTUDIO_TIMEOUT_SECONDS)
        self._lms = lms
        self._model = lms.llm(model_name)

    @staticmethod
    def _stat(stats: Any, *names: str) -> int | None:
        for name in names:
            value = getattr(stats, name, None)
            if isinstance(value, (int, float)):
                return int(value)
        return None

    def respond(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        chat = self._lms.Chat(system_prompt)
        chat.add_user_message(user_prompt)
        result = self._model.respond(
            chat,
            config={"temperature": config.LLM_TEMPERATURE, "maxTokens": config.LLM_MAX_TOKENS},
        )
        stats = getattr(result, "stats", None)
        return {
            "text": str(getattr(result, "content", result) or ""),
            "prompt_tokens": self._stat(stats, "prompt_tokens_count", "promptTokensCount"),
            "completion_tokens": self._stat(stats, "predicted_tokens_count", "predictedTokensCount"),
            "finish_reason": str(getattr(stats, "stop_reason", "") or ""),
        }


class LLMClient:
    def __init__(self, model_name: str = config.MODEL_NAME, backend: Any = None) -> None:
        self.model_name = model_name
        self._backend = backend

    @property
    def backend(self) -> Any:
        if self._backend is None:
            try:
                self._backend = LMStudioBackend(self.model_name)
            except Exception as exc:  # noqa: BLE001 — message actionnable pour l'utilisateur
                raise RuntimeError(
                    "Impossible de joindre LM Studio. Vérifiez que l'application est ouverte, "
                    "que le serveur est démarré (onglet Developer) et que le modèle "
                    f"{self.model_name} est chargé."
                ) from exc
        return self._backend

    def complete(self, system_prompt: str, user_prompt: str) -> LLMResult:
        last_error = ""
        for attempt in range(1, config.LLM_MAX_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                payload = self.backend.respond(system_prompt, user_prompt)
                elapsed = time.perf_counter() - started
                raw = str(payload.get("text") or "")
                return LLMResult(
                    text=clean_answer(raw),
                    raw_text=raw,
                    response_time=elapsed,
                    prompt_tokens=payload.get("prompt_tokens"),
                    completion_tokens=payload.get("completion_tokens"),
                    finish_reason=str(payload.get("finish_reason") or ""),
                    status="ok",
                    error="",
                    attempt=attempt,
                )
            except Exception as exc:  # noqa: BLE001 — une panne ne doit pas arrêter le run
                elapsed = time.perf_counter() - started
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < config.LLM_MAX_ATTEMPTS:
                    time.sleep(min(2 ** attempt, 10))

        return LLMResult(
            text="",
            raw_text="",
            response_time=elapsed,
            prompt_tokens=None,
            completion_tokens=None,
            finish_reason="",
            status="error",
            error=last_error,
            attempt=config.LLM_MAX_ATTEMPTS,
        )
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Vérifier les statistiques réellement exposées par le SDK**

LM Studio doit tourner avec un modèle chargé.

Run: `python -c "
from src.llm_client import LLMClient
r = LLMClient().complete('Answer with the answer only.', 'Question: Capital of France?\nAnswer:')
print(r)
"`
Expected: `status='ok'`, texte non vide. Si `prompt_tokens` et `completion_tokens` sont `None`, inspecter `dir(result.stats)` dans `LMStudioBackend.respond` et compléter la liste de noms passée à `_stat`. Le correctif 9 n'est acquis que si ces compteurs sont peuplés.

---

### Task 9: Inférence reprenable et partitionnée

Cœur du plan. Corrige la clé de reprise, l'écriture quadratique, l'absence de provenance et le traitement des erreurs.

**Files:**
- Modify: `src/enrich_llm.py` (réécriture complète)
- Test: `tests/test_enrich_llm.py`

**Interfaces:**
- Consumes: `src.prompts`, `src.llm_client`, `src.runmeta`, `src.io_utils`, `config`.
- Produces: `partition_dir(model_slug: str, variant_id: str) -> Path`, `load_done_keys(model_slug: str, variant_id: str) -> set[str]`, `select_pending(questions: pandas.DataFrame, done: set[str], shard: tuple[int, int] | None) -> pandas.DataFrame`, `stratified_sample(df, sample_size: int, seed: int) -> pandas.DataFrame`, `run_enrich(model_name: str, variant_ids: list[str], sample_size: int = 0, shard: tuple[int, int] | None = None, client=None) -> list[Path]`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_enrich_llm.py` :

```python
import pandas as pd
import pytest

from src.enrich_llm import (
    load_done_keys,
    partition_dir,
    run_enrich,
    select_pending,
    stratified_sample,
)
from src.llm_client import LLMResult


@pytest.fixture
def questions():
    rows = []
    for i in range(6):
        rows.append(
            {
                "question_id": f"q{i:04d}",
                "category": "Art",
                "category_group": "General",
                "category_name": "Art",
                "type": "multiple",
                "difficulty": ["easy", "medium", "hard"][i % 3],
                "question": f"Question {i}?",
                "correct_answer": "A",
                "incorrect_answers": ["B", "C", "D"],
                "choices": ["B", "A", "C", "D"],
                "correct_answer_position": 1,
                "n_choices": 4,
                "correct_answer_norm": "a",
                "answer_is_numeric": False,
                "question_len": 12,
                "answer_len": 1,
                "cleaned_at": "2026-09-10T10:00:00+00:00",
            }
        )
    return pd.DataFrame(rows)


class StubClient:
    def __init__(self, model_name="fake/model", failures=()):
        self.model_name = model_name
        self.calls = []
        self.failures = set(failures)

    def complete(self, system_prompt, user_prompt):
        self.calls.append(user_prompt)
        index = len(self.calls) - 1
        if index in self.failures:
            return LLMResult("", "", 0.1, None, None, "", "error", "boom", 3)
        return LLMResult("A", "A", 0.2, 30, 2, "stop", "ok", "", 1)


@pytest.fixture(autouse=True)
def silver_dirs(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "SILVER_ANSWERS_DIR", tmp_path / "answers")
    monkeypatch.setattr(config, "SILVER_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "SILVER_QUESTIONS", tmp_path / "questions.parquet")
    monkeypatch.setattr(config, "FLUSH_EVERY", 2)
    return tmp_path


def test_partition_path_encodes_model_and_variant():
    path = partition_dir("google_gemma", "p1_constrained_mcq")
    assert path.parts[-2:] == ("model=google_gemma", "prompt_variant=p1_constrained_mcq")


def test_two_models_do_not_share_a_partition():
    assert partition_dir("a", "p1_constrained_mcq") != partition_dir("b", "p1_constrained_mcq")


def test_select_pending_excludes_done_keys(questions):
    pending = select_pending(questions, {"q0000", "q0001"}, None)
    assert set(pending["question_id"]) == {"q0002", "q0003", "q0004", "q0005"}


def test_select_pending_shards_deterministically(questions):
    first = select_pending(questions, set(), (0, 3))
    second = select_pending(questions, set(), (1, 3))
    third = select_pending(questions, set(), (2, 3))
    ids = set(first["question_id"]) | set(second["question_id"]) | set(third["question_id"])
    assert ids == set(questions["question_id"])
    assert not set(first["question_id"]) & set(second["question_id"])


def test_run_enrich_writes_one_partition_per_variant(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq", "p2_open_minimal"], client=StubClient())
    frame = pd.read_parquet(silver_dirs / "answers")
    assert len(frame) == 12
    assert set(frame["prompt_variant"]) == {"p1_constrained_mcq", "p2_open_minimal"}


def test_run_enrich_flushes_several_parts(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    parts = list(partition_dir("fake_model", "p1_constrained_mcq").glob("part-*.parquet"))
    assert len(parts) == 3


def test_rerun_is_idempotent(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    second_client = StubClient()
    run_enrich("fake/model", ["p1_constrained_mcq"], client=second_client)
    assert second_client.calls == []
    assert len(pd.read_parquet(silver_dirs / "answers")) == 6


def test_a_second_model_does_not_overwrite_the_first(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("model/one", ["p1_constrained_mcq"], client=StubClient("model/one"))
    run_enrich("model/two", ["p1_constrained_mcq"], client=StubClient("model/two"))
    frame = pd.read_parquet(silver_dirs / "answers")
    assert len(frame) == 12
    assert set(frame["model_name"]) == {"model/one", "model/two"}


def test_errored_rows_are_retried_on_the_next_run(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient(failures={0, 1}))
    done = load_done_keys("fake_model", "p1_constrained_mcq")
    assert len(done) == 4
    retry_client = StubClient()
    run_enrich("fake/model", ["p1_constrained_mcq"], client=retry_client)
    assert len(retry_client.calls) == 2


def test_first_row_of_a_run_is_flagged_as_warmup(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    frame = pd.read_parquet(silver_dirs / "answers")
    assert frame["is_warmup"].sum() == 1


def test_provenance_columns_are_populated(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    frame = pd.read_parquet(silver_dirs / "answers")
    for column in ("host", "hardware", "run_id", "prompt_hash", "model_slug"):
        assert frame[column].notna().all()
        assert (frame[column].astype(str).str.len() > 0).all()


def test_run_metadata_file_is_written(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    assert list((silver_dirs / "runs").glob("run-*.json"))


def test_stratified_sample_respects_proportions(questions):
    sampled = stratified_sample(questions, 3, 42)
    assert len(sampled) == 3
    assert sampled["difficulty"].nunique() == 3
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_enrich_llm.py -v`
Expected: FAIL, `ImportError: cannot import name 'partition_dir'`.

- [ ] **Step 3: Réécrire `src/enrich_llm.py`**

```python
"""Inférence : interroge un modèle sur les questions silver.

Trois propriétés non négociables à cette échelle, environ 31 800 appels :

- la clé fonctionnelle est le triplet (question_id, model_slug, prompt_variant),
  sans quoi un second modèle écrase les résultats du premier ;
- l'écriture est incrémentale par lots, sans jamais relire l'existant, sans quoi
  le coût d'entrée-sortie dépasse le coût d'inférence ;
- une erreur d'infrastructure ne vaut pas mauvaise réponse : la ligne est
  conservée pour traçabilité mais repasse dans la file au run suivant.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src.io_utils import atomic_write_dataframe, atomic_write_json
from src.llm_client import LLMClient
from src.prompts import get_variant
from src.runmeta import host_info, model_slug, new_run_id

ANSWER_COLUMNS = [
    "question_id",
    "model_slug",
    "prompt_variant",
    "model_name",
    "prompt_hash",
    "prompt_text",
    "raw_answer",
    "ai_answer",
    "response_time",
    "prompt_tokens",
    "completion_tokens",
    "finish_reason",
    "status",
    "error",
    "attempt",
    "is_warmup",
    "answered_at",
    "run_id",
    "host",
    "hardware",
    "os_version",
    "python_version",
    "runtime_version",
    "temperature",
    "max_tokens",
]


def _shard_of(question_id: str, total: int) -> int:
    """Répartition déterministe et indépendante du format de l'identifiant."""
    digest = hashlib.sha256(question_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % total


def partition_dir(slug: str, variant_id: str) -> Path:
    return config.SILVER_ANSWERS_DIR / f"model={slug}" / f"prompt_variant={variant_id}"


def load_done_keys(slug: str, variant_id: str) -> set[str]:
    """question_id déjà traités avec succès pour ce couple modèle/variante."""
    directory = partition_dir(slug, variant_id)
    parts = sorted(directory.glob("part-*.parquet"))
    if not parts:
        return set()
    frames = [pd.read_parquet(part, columns=["question_id", "status"]) for part in parts]
    frame = pd.concat(frames, ignore_index=True)
    return set(frame.loc[frame["status"] == "ok", "question_id"].astype(str))


def select_pending(
    questions: pd.DataFrame, done: set[str], shard: tuple[int, int] | None
) -> pd.DataFrame:
    pending = questions[~questions["question_id"].astype(str).isin(done)]
    if shard is not None:
        index, total = shard
        keep = pending["question_id"].astype(str).map(lambda qid: _shard_of(qid, total) == index)
        pending = pending[keep]
    return pending.reset_index(drop=True)


def stratified_sample(df: pd.DataFrame, sample_size: int, seed: int) -> pd.DataFrame:
    """Échantillon proportionnel par category × difficulty × type.

    Réservé aux smoke tests. L'élagage final tire une ligne par strate à tour
    de rôle plutôt qu'au hasard, ce qui préserve les proportions.
    """
    if sample_size <= 0 or sample_size >= len(df):
        return df.reset_index(drop=True)

    work = df.copy()
    work["_stratum"] = (
        work["category"].astype(str) + "|" + work["difficulty"].astype(str) + "|" + work["type"].astype(str)
    )
    groups = [group.sample(frac=1.0, random_state=seed) for _, group in work.groupby("_stratum", sort=True)]

    picked: list[pd.DataFrame] = []
    taken = 0
    position = 0
    while taken < sample_size:
        progressed = False
        for group in groups:
            if position < len(group) and taken < sample_size:
                picked.append(group.iloc[[position]])
                taken += 1
                progressed = True
        if not progressed:
            break
        position += 1

    return pd.concat(picked, ignore_index=True).drop(columns=["_stratum"]).reset_index(drop=True)


def _flush(buffer: list[dict[str, Any]], directory: Path, run_id: str, sequence: int) -> None:
    if not buffer:
        return
    frame = pd.DataFrame(buffer, columns=ANSWER_COLUMNS)
    atomic_write_dataframe(frame, directory / f"part-{run_id}-{sequence:05d}.parquet", "parquet")


def run_enrich(
    model_name: str = config.MODEL_NAME,
    variant_ids: list[str] | None = None,
    *,
    sample_size: int = 0,
    shard: tuple[int, int] | None = None,
    client: Any = None,
) -> list[Path]:
    if not config.SILVER_QUESTIONS.exists():
        raise FileNotFoundError(f"Silver introuvable: {config.SILVER_QUESTIONS}")

    variant_ids = variant_ids or list(("p1_constrained_mcq", "p2_open_minimal", "p3_open_guided"))
    client = client or LLMClient(model_name)
    slug = model_slug(model_name)
    run_id = new_run_id()
    provenance = host_info()
    questions = pd.read_parquet(config.SILVER_QUESTIONS)
    if sample_size:
        questions = stratified_sample(questions, sample_size, config.SAMPLE_SEED)

    written: list[Path] = []
    first_call_of_run = True
    counters = {"ok": 0, "error": 0}

    for variant_id in variant_ids:
        variant = get_variant(variant_id)
        directory = partition_dir(slug, variant_id)
        done = load_done_keys(slug, variant_id)
        pending = select_pending(questions, done, shard)
        print(f"[enrich] {model_name} / {variant_id}: {len(pending)} à traiter, {len(done)} déjà faites.")

        buffer: list[dict[str, Any]] = []
        sequence = 0
        for row in tqdm(pending.to_dict(orient="records"), desc=variant_id, unit="q"):
            user_prompt = variant.build_user_prompt(row)
            result = client.complete(variant.system_prompt, user_prompt)
            counters["ok" if result.status == "ok" else "error"] += 1

            buffer.append(
                {
                    "question_id": row["question_id"],
                    "model_slug": slug,
                    "prompt_variant": variant_id,
                    "model_name": model_name,
                    "prompt_hash": variant.prompt_hash,
                    "prompt_text": user_prompt,
                    "raw_answer": result.raw_text,
                    "ai_answer": result.text,
                    "response_time": round(result.response_time, 4),
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "finish_reason": result.finish_reason,
                    "status": result.status,
                    "error": result.error,
                    "attempt": result.attempt,
                    "is_warmup": first_call_of_run,
                    "answered_at": datetime.now(timezone.utc).isoformat(),
                    "run_id": run_id,
                    **provenance,
                    "temperature": config.LLM_TEMPERATURE,
                    "max_tokens": config.LLM_MAX_TOKENS,
                }
            )
            first_call_of_run = False

            if len(buffer) >= config.FLUSH_EVERY:
                _flush(buffer, directory, run_id, sequence)
                written.append(directory / f"part-{run_id}-{sequence:05d}.parquet")
                buffer.clear()
                sequence += 1

        if buffer:
            _flush(buffer, directory, run_id, sequence)
            written.append(directory / f"part-{run_id}-{sequence:05d}.parquet")

    atomic_write_json(
        config.SILVER_RUNS_DIR / f"run-{run_id}.json",
        {
            "run_id": run_id,
            "model_name": model_name,
            "model_slug": slug,
            "variants": variant_ids,
            "shard": list(shard) if shard else None,
            "sample_size": sample_size,
            "counters": counters,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            **provenance,
        },
    )
    print(f"[enrich] {counters['ok']} réponses, {counters['error']} erreurs, run {run_id}.")
    return written


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Inférence LM Studio sur les questions silver.")
    parser.add_argument("--model", default=config.MODEL_NAME)
    parser.add_argument("--variants", nargs="*", default=None)
    parser.add_argument("--sample-size", type=int, default=0)
    parser.add_argument("--shard", default=None, help="Format i/n, par exemple 0/3.")
    args = parser.parse_args()

    shard = None
    if args.shard:
        index, total = args.shard.split("/")
        shard = (int(index), int(total))

    run_enrich(args.model, args.variants, sample_size=args.sample_size, shard=shard)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_enrich_llm.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Smoke test réel**

Run: `python -m src.enrich_llm --sample-size 20 --variants p1_constrained_mcq`
Expected: 20 réponses écrites dans `data/silver/answers/model=…/prompt_variant=p1_constrained_mcq/`, un fichier de run dans `data/silver/runs/`. Relancer la même commande ne doit produire aucun appel supplémentaire.

---

### Task 10: Primitives de comparaison

**Files:**
- Modify: `src/scoring.py` (réécriture complète)
- Test: `tests/test_scoring.py`

**Interfaces:**
- Consumes: `config`.
- Produces: `normalize(text: object) -> str`, `boolean_label(text: str) -> bool | None`, `resolve_choice_reference(answer: str, choices: list[str]) -> str | None`, `fuzzy_score(a: str, b: str) -> float`, `matches_single_choice(answer_norm: str, choices: list[str]) -> str | None`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_scoring.py` :

```python
import pytest

from src.scoring import (
    boolean_label,
    fuzzy_score,
    matches_single_choice,
    normalize,
    resolve_choice_reference,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Léonard  de Vinci ", "leonard de vinci"),
        ("The Beatles", "beatles"),
        ("A Clockwork Orange", "clockwork orange"),
        ("Rock & Roll!", "rock roll"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_normalize_keeps_digits():
    assert normalize("1789") == "1789"


@pytest.mark.parametrize("raw,expected", [("True", True), ("false", False), ("yes", True),
                                          ("NO", False), ("vrai", True), ("maybe", None)])
def test_boolean_label(raw, expected):
    assert boolean_label(raw) is expected


def test_boolean_label_reads_the_first_token():
    assert boolean_label("True, because gravity") is True


def test_resolve_choice_reference_by_letter():
    choices = ["Paris", "Rome", "Berlin"]
    assert resolve_choice_reference("B", choices) == "Rome"


def test_resolve_choice_reference_by_rank():
    choices = ["Paris", "Rome", "Berlin"]
    assert resolve_choice_reference("option 3", choices) == "Berlin"


def test_resolve_choice_reference_ignores_plain_answers():
    assert resolve_choice_reference("Berlin", ["Paris", "Rome", "Berlin"]) is None


def test_resolve_choice_reference_rejects_out_of_range():
    assert resolve_choice_reference("Z", ["Paris", "Rome"]) is None


def test_matches_single_choice_returns_the_option():
    assert matches_single_choice("rome", ["Paris", "Rome", "Berlin"]) == "Rome"


def test_matches_single_choice_returns_none_when_ambiguous():
    assert matches_single_choice("rome", ["Rome", "rome!"]) is None


def test_fuzzy_score_is_high_for_name_variants():
    assert fuzzy_score("leonardo da vinci", "da vinci") >= 90


def test_fuzzy_score_is_symmetric():
    assert fuzzy_score("abc def", "def abc") == fuzzy_score("def abc", "abc def")
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: FAIL, `ImportError: cannot import name 'boolean_label'`.

- [ ] **Step 3: Réécrire `src/scoring.py`**

```python
"""Primitives de comparaison de réponses.

Ce module ne rend aucun verdict : il fournit les briques que la cascade de
src/judge.py assemble. La règle de sous-chaîne de la version précédente est
supprimée, trop généreuse : « Paris » validait « Paris Hilton ».
"""

from __future__ import annotations

import re
import string
import unicodedata

from rapidfuzz import fuzz

_TRUE = {"true", "yes", "y", "t", "1", "vrai", "oui"}
_FALSE = {"false", "no", "n", "f", "0", "faux", "non"}
_LEADING_ARTICLES = ("the ", "a ", "an ")
_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")
_LETTER_REF = re.compile(r"^(?:option\s*|answer\s*)?([a-z])$", re.IGNORECASE)
_RANK_REF = re.compile(r"^(?:option\s*|answer\s*|number\s*)?(\d{1,2})\.?$", re.IGNORECASE)


def normalize(text: object) -> str:
    if text is None:
        return ""
    value = unicodedata.normalize("NFKD", str(text))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = _PUNCT.sub(" ", value.casefold())
    value = _SPACES.sub(" ", value).strip()
    for article in _LEADING_ARTICLES:
        if value.startswith(article):
            return value[len(article):].strip()
    return value


def boolean_label(text: str) -> bool | None:
    token = normalize(text)
    if not token:
        return None
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    first = token.split(" ", 1)[0]
    if first in _TRUE:
        return True
    if first in _FALSE:
        return False
    return None


def resolve_choice_reference(answer: str, choices: list[str]) -> str | None:
    """Résout une réponse qui désigne une option par sa lettre ou son rang."""
    candidate = str(answer).strip()
    if not candidate or len(candidate) > 10:
        return None

    letter = _LETTER_REF.match(candidate)
    if letter:
        index = string.ascii_lowercase.index(letter.group(1).lower())
        return choices[index] if index < len(choices) else None

    rank = _RANK_REF.match(candidate)
    if rank:
        index = int(rank.group(1)) - 1
        return choices[index] if 0 <= index < len(choices) else None

    return None


def matches_single_choice(answer_norm: str, choices: list[str]) -> str | None:
    hits = [choice for choice in choices if normalize(choice) == answer_norm]
    return hits[0] if len(hits) == 1 else None


def fuzzy_score(a: str, b: str) -> float:
    return float(fuzz.token_set_ratio(a, b))
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: PASS, 19 tests (paramétrages compris).

---

### Task 11: Cascade de jugement

**Files:**
- Create: `src/judge.py`
- Test: `tests/test_judge.py`

**Interfaces:**
- Consumes: `src.scoring`, `src.llm_client`, `src.io_utils`, `config`.
- Produces: `judge_one(answer_row: Mapping, question_row: Mapping, llm_judge=None) -> dict`, `run_judge(model_slug: str | None = None, llm_judge=None) -> list[Path]`, `LLMJudge(client)` avec `is_equivalent(question: str, expected: str, given: str) -> bool`.

Le dictionnaire renvoyé par `judge_one` contient `match_method`, `ai_correct_strict`, `ai_correct`, `fuzzy_score`, `ai_answer_norm`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_judge.py` :

```python
import pandas as pd
import pytest

from src.judge import LLMJudge, judge_one, run_judge


def question(**overrides):
    row = {
        "question_id": "q1",
        "question": "Who painted the Mona Lisa?",
        "type": "multiple",
        "correct_answer": "Leonardo da Vinci",
        "correct_answer_norm": "leonardo da vinci",
        "choices": ["Raphael", "Leonardo da Vinci", "Titian", "Donatello"],
        "answer_is_numeric": False,
    }
    row.update(overrides)
    return row


def answer(text, status="ok"):
    return {"question_id": "q1", "ai_answer": text, "status": status}


class AlwaysYesJudge:
    def __init__(self):
        self.calls = 0

    def is_equivalent(self, question, expected, given):
        self.calls += 1
        return True


class AlwaysNoJudge:
    def __init__(self):
        self.calls = 0

    def is_equivalent(self, question, expected, given):
        self.calls += 1
        return False


def test_error_status_is_excluded():
    verdict = judge_one(answer("", status="error"), question())
    assert verdict["match_method"] == "error"
    assert verdict["ai_correct"] is None
    assert verdict["ai_correct_strict"] is None


def test_exact_match():
    verdict = judge_one(answer("Leonardo da Vinci"), question())
    assert verdict["match_method"] == "exact"
    assert verdict["ai_correct_strict"] is True
    assert verdict["ai_correct"] is True


def test_exact_match_ignores_case_and_articles():
    verdict = judge_one(answer("the beatles"), question(correct_answer="The Beatles",
                                                        correct_answer_norm="beatles"))
    assert verdict["match_method"] == "exact"


def test_boolean_true_false():
    row = question(type="boolean", correct_answer="True", correct_answer_norm="true",
                   choices=["True", "False"])
    assert judge_one(answer("True"), row)["match_method"] == "boolean"
    assert judge_one(answer("True"), row)["ai_correct"] is True
    assert judge_one(answer("False"), row)["ai_correct"] is False


def test_choice_letter_is_resolved():
    verdict = judge_one(answer("B"), question())
    assert verdict["match_method"] == "choice_letter"
    assert verdict["ai_correct"] is True


def test_choice_letter_pointing_elsewhere_is_wrong():
    verdict = judge_one(answer("A"), question())
    assert verdict["match_method"] == "choice_letter"
    assert verdict["ai_correct"] is False


def test_fuzzy_rescues_a_name_variant():
    judge = AlwaysNoJudge()
    verdict = judge_one(answer("Da Vinci"), question(), llm_judge=judge)
    assert verdict["match_method"] == "fuzzy"
    assert verdict["ai_correct"] is True
    assert verdict["ai_correct_strict"] is False
    assert judge.calls == 0


def test_fuzzy_is_disabled_for_numeric_answers():
    row = question(correct_answer="1789", correct_answer_norm="1789", answer_is_numeric=True,
                   choices=["1789", "1798", "1801", "1812"])
    judge = AlwaysNoJudge()
    verdict = judge_one(answer("1798"), row, llm_judge=judge)
    assert verdict["match_method"] != "fuzzy"
    assert verdict["ai_correct"] is False


def test_llm_judge_is_the_last_resort():
    judge = AlwaysYesJudge()
    verdict = judge_one(answer("the painter from Vinci"), question(), llm_judge=judge)
    assert verdict["match_method"] == "llm_judge"
    assert verdict["ai_correct"] is True
    assert verdict["ai_correct_strict"] is False
    assert judge.calls == 1


def test_no_match_when_judge_declines():
    verdict = judge_one(answer("Pablo Picasso"), question(), llm_judge=AlwaysNoJudge())
    assert verdict["match_method"] == "no_match"
    assert verdict["ai_correct"] is False


def test_permissive_never_below_strict():
    for text in ["Leonardo da Vinci", "Da Vinci", "B", "Pablo Picasso"]:
        verdict = judge_one(answer(text), question(), llm_judge=AlwaysNoJudge())
        if verdict["ai_correct_strict"]:
            assert verdict["ai_correct"]


def test_llm_judge_parses_yes_and_no():
    class Stub:
        def __init__(self, reply):
            self.reply = reply

        def complete(self, system_prompt, user_prompt):
            from src.llm_client import LLMResult

            return LLMResult(self.reply, self.reply, 0.1, None, None, "stop", "ok", "", 1)

    assert LLMJudge(Stub("YES")).is_equivalent("q", "a", "b") is True
    assert LLMJudge(Stub("no")).is_equivalent("q", "a", "b") is False
    assert LLMJudge(Stub("perhaps")).is_equivalent("q", "a", "b") is False


def test_run_judge_writes_partitions(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "SILVER_ANSWERS_DIR", tmp_path / "answers")
    monkeypatch.setattr(config, "SILVER_JUDGMENTS_DIR", tmp_path / "judgments")
    monkeypatch.setattr(config, "SILVER_QUESTIONS", tmp_path / "questions.parquet")

    pd.DataFrame([question()]).to_parquet(tmp_path / "questions.parquet", index=False)
    part = tmp_path / "answers" / "model=m1" / "prompt_variant=p1_constrained_mcq"
    part.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "question_id": "q1",
                "model_slug": "m1",
                "prompt_variant": "p1_constrained_mcq",
                "ai_answer": "Leonardo da Vinci",
                "status": "ok",
            }
        ]
    ).to_parquet(part / "part-001.parquet", index=False)

    run_judge()
    judged = pd.read_parquet(tmp_path / "judgments")
    assert len(judged) == 1
    assert judged.loc[0, "match_method"] == "exact"
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_judge.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.judge'`.

- [ ] **Step 3: Implémenter**

Créer `src/judge.py` :

```python
"""Cascade de jugement : verdict par niveaux, traçable.

Séparée de l'inférence pour être rejouable : ajuster un seuil ou le prompt du
juge ne doit pas coûter une seconde d'inférence. Deux taux sont produits, strict
et permissif ; l'écart entre les deux, ventilé par match_method, est un résultat
en soi plutôt qu'un biais caché.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src.io_utils import atomic_write_dataframe
from src.llm_client import LLMClient
from src.scoring import (
    boolean_label,
    fuzzy_score,
    matches_single_choice,
    normalize,
    resolve_choice_reference,
)

STRICT_METHODS = {"boolean", "exact", "choice_letter", "choice_match"}

JUDGMENT_COLUMNS = [
    "question_id",
    "model_slug",
    "prompt_variant",
    "ai_answer_norm",
    "match_method",
    "ai_correct_strict",
    "ai_correct",
    "fuzzy_score",
    "judge_model",
    "judgment_version",
    "judged_at",
]

_JUDGE_SYSTEM = (
    "You decide whether a candidate answer means the same as the reference answer "
    "for a trivia question. Reply with exactly one word: YES or NO."
)


class LLMJudge:
    def __init__(self, client: Any = None) -> None:
        self.client = client or LLMClient(config.JUDGE_MODEL_NAME)

    def is_equivalent(self, question: str, expected: str, given: str) -> bool:
        prompt = (
            f"Question: {question}\n"
            f"Reference answer: {expected}\n"
            f"Candidate answer: {given}\n"
            "Does the candidate answer mean the same as the reference answer? YES or NO."
        )
        result = self.client.complete(_JUDGE_SYSTEM, prompt)
        if result.status != "ok":
            return False
        return normalize(result.text).split(" ")[0] == "yes"


def _verdict(method: str, correct: bool, answer_norm: str, score: float) -> dict[str, Any]:
    return {
        "match_method": method,
        "ai_correct_strict": correct if method in STRICT_METHODS else False,
        "ai_correct": correct,
        "ai_answer_norm": answer_norm,
        "fuzzy_score": score,
    }


def judge_one(
    answer_row: Mapping[str, Any],
    question_row: Mapping[str, Any],
    llm_judge: Any = None,
) -> dict[str, Any]:
    if str(answer_row.get("status", "ok")) != "ok":
        return {
            "match_method": "error",
            "ai_correct_strict": None,
            "ai_correct": None,
            "ai_answer_norm": "",
            "fuzzy_score": 0.0,
        }

    given = str(answer_row.get("ai_answer") or "")
    answer_norm = normalize(given)
    expected_norm = str(question_row["correct_answer_norm"])
    choices = list(question_row["choices"])

    if not answer_norm:
        return _verdict("no_match", False, answer_norm, 0.0)

    if question_row["type"] == "boolean":
        given_bool = boolean_label(given)
        expected_bool = boolean_label(str(question_row["correct_answer"]))
        if given_bool is not None and expected_bool is not None:
            return _verdict("boolean", given_bool is expected_bool, answer_norm, 0.0)

    if answer_norm == expected_norm:
        return _verdict("exact", True, answer_norm, 100.0)

    referenced = resolve_choice_reference(given, choices)
    if referenced is not None:
        return _verdict("choice_letter", normalize(referenced) == expected_norm, answer_norm, 0.0)

    single = matches_single_choice(answer_norm, choices)
    if single is not None:
        return _verdict("choice_match", normalize(single) == expected_norm, answer_norm, 0.0)

    score = fuzzy_score(answer_norm, expected_norm)
    if not bool(question_row.get("answer_is_numeric")) and score >= config.FUZZY_RATIO_THRESHOLD:
        return _verdict("fuzzy", True, answer_norm, score)

    if llm_judge is not None:
        equivalent = llm_judge.is_equivalent(
            str(question_row["question"]), str(question_row["correct_answer"]), given
        )
        if equivalent:
            return _verdict("llm_judge", True, answer_norm, score)

    return _verdict("no_match", False, answer_norm, score)


def run_judge(model_slug: str | None = None, llm_judge: Any = None) -> list[Path]:
    if not config.SILVER_QUESTIONS.exists():
        raise FileNotFoundError(f"Silver introuvable: {config.SILVER_QUESTIONS}")

    questions = pd.read_parquet(config.SILVER_QUESTIONS).set_index("question_id", drop=False)
    pattern = f"model={model_slug}" if model_slug else "model=*"
    written: list[Path] = []

    for variant_dir in sorted(config.SILVER_ANSWERS_DIR.glob(f"{pattern}/prompt_variant=*")):
        parts = sorted(variant_dir.glob("part-*.parquet"))
        if not parts:
            continue
        answers = pd.concat([pd.read_parquet(part) for part in parts], ignore_index=True)
        slug = variant_dir.parent.name.split("=", 1)[1]
        variant_id = variant_dir.name.split("=", 1)[1]

        records = []
        now = datetime.now(timezone.utc).isoformat()
        for answer_row in tqdm(answers.to_dict(orient="records"), desc=f"{slug}/{variant_id}", unit="a"):
            question_row = questions.loc[answer_row["question_id"]]
            verdict = judge_one(answer_row, question_row, llm_judge)
            records.append(
                {
                    "question_id": answer_row["question_id"],
                    "model_slug": slug,
                    "prompt_variant": variant_id,
                    "judge_model": config.JUDGE_MODEL_NAME,
                    "judgment_version": config.JUDGMENT_VERSION,
                    "judged_at": now,
                    **verdict,
                }
            )

        target = config.SILVER_JUDGMENTS_DIR / f"model={slug}" / f"prompt_variant={variant_id}"
        path = target / f"judgments-{config.JUDGMENT_VERSION}.parquet"
        atomic_write_dataframe(pd.DataFrame(records, columns=JUDGMENT_COLUMNS), path, "parquet")
        written.append(path)

    print(f"[judge] {len(written)} partitions écrites sous {config.SILVER_JUDGMENTS_DIR}")
    return written


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Jugement des réponses silver.")
    parser.add_argument("--model-slug", default=None)
    parser.add_argument("--no-llm-judge", action="store_true", help="Cascade sans le dernier niveau.")
    args = parser.parse_args()
    run_judge(args.model_slug, None if args.no_llm_judge else LLMJudge())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_judge.py -v`
Expected: PASS, 15 tests.

---

### Task 12: CLI d'orchestration

**Files:**
- Modify: `run_pipeline.py` (réécriture complète)
- Test: `tests/test_run_pipeline.py`

**Interfaces:**
- Consumes: `src.ingest_opentdb.run_ingest`, `src.transform_silver.run_transform`, `src.enrich_llm.run_enrich`, `src.judge.run_judge`.
- Produces: `parse_args(argv) -> argparse.Namespace`, `parse_shard(value: str | None) -> tuple[int, int] | None`, `main(argv) -> None`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_run_pipeline.py` :

```python
import pytest

from run_pipeline import parse_args, parse_shard


def test_default_runs_every_stage():
    args = parse_args([])
    assert args.stages == ["ingest", "transform", "enrich", "judge"]


def test_stage_selection():
    args = parse_args(["--stages", "enrich", "judge"])
    assert args.stages == ["enrich", "judge"]


def test_unknown_stage_is_rejected():
    with pytest.raises(SystemExit):
        parse_args(["--stages", "gold"])


def test_variants_default_to_all_three():
    args = parse_args([])
    assert args.variants == ["p1_constrained_mcq", "p2_open_minimal", "p3_open_guided"]


def test_parse_shard():
    assert parse_shard("1/3") == (1, 3)
    assert parse_shard(None) is None


def test_parse_shard_rejects_out_of_range():
    with pytest.raises(ValueError):
        parse_shard("3/3")


def test_parse_shard_rejects_garbage():
    with pytest.raises(ValueError):
        parse_shard("abc")
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_run_pipeline.py -v`
Expected: FAIL, `ImportError: cannot import name 'parse_shard'`.

- [ ] **Step 3: Réécrire `run_pipeline.py`**

```python
#!/usr/bin/env python3
"""Orchestration : ingest → transform → enrich → judge.

Les étages sont sélectionnables individuellement : l'inférence dure plusieurs
heures et se répartit entre postes, alors que le jugement se rejoue en minutes.
"""

from __future__ import annotations

import argparse
import sys

import config

STAGES = ["ingest", "transform", "enrich", "judge"]
DEFAULT_VARIANTS = ["p1_constrained_mcq", "p2_open_minimal", "p3_open_guided"]


def parse_shard(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    try:
        index_text, total_text = value.split("/")
        index, total = int(index_text), int(total_text)
    except ValueError as exc:
        raise ValueError(f"Shard invalide: {value!r}. Format attendu i/n, par exemple 0/3.") from exc
    if total < 1 or not 0 <= index < total:
        raise ValueError(f"Shard hors bornes: {value!r}. Attendu 0 <= i < n.")
    return index, total


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pipeline de benchmark LLM sur OpenTDB.")
    parser.add_argument("--stages", nargs="+", choices=STAGES, default=list(STAGES))
    parser.add_argument("--model", default=config.MODEL_NAME)
    parser.add_argument("--variants", nargs="+", default=list(DEFAULT_VARIANTS))
    parser.add_argument("--sample-size", type=int, default=0, help="0 = tout le dataset.")
    parser.add_argument("--shard", default=None, help="Découpe le travail: i/n.")
    parser.add_argument("--force-ingest", action="store_true")
    parser.add_argument("--no-llm-judge", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    shard = parse_shard(args.shard)

    if "ingest" in args.stages:
        from src.ingest_opentdb import run_ingest

        run_ingest(force=args.force_ingest)

    if "transform" in args.stages:
        from src.transform_silver import run_transform

        run_transform()

    if "enrich" in args.stages:
        from src.enrich_llm import run_enrich

        run_enrich(args.model, args.variants, sample_size=args.sample_size, shard=shard)

    if "judge" in args.stages:
        from src.judge import LLMJudge, run_judge

        run_judge(llm_judge=None if args.no_llm_judge else LLMJudge())


if __name__ == "__main__":
    main(sys.argv[1:])
```

- [ ] **Step 4: Lancer la suite complète**

Run: `python -m pytest -v`
Expected: PASS, tous les tests des tâches 1 à 12.

---

### Task 13: Projet dbt et couche staging

**Files:**
- Create: `dbt_project/dbt_project.yml`, `dbt_project/profiles.yml`, `dbt_project/models/staging/sources.yml`, `dbt_project/models/staging/stg_questions.sql`, `dbt_project/models/staging/stg_answers.sql`, `dbt_project/models/staging/stg_judgments.sql`, `dbt_project/models/staging/schema.yml`
- Create: `dbt_project/models/intermediate/int_results.sql`, `dbt_project/models/intermediate/schema.yml`

**Interfaces:**
- Consumes: les parquets silver des tâches 6, 9 et 11.
- Produces: les relations `stg_questions`, `stg_answers`, `stg_judgments`, `int_results` dans `data/gold/benchmark.duckdb`. `int_results` porte une ligne par `(question_id, model_slug, prompt_variant)` avec les colonnes de question, de réponse et de verdict.

- [ ] **Step 1: Créer la configuration dbt**

`dbt_project/dbt_project.yml` :

```yaml
name: trivia_benchmark
version: "1.0.0"
config-version: 2
profile: trivia_benchmark

model-paths: ["models"]
target-path: "target"
clean-targets: ["target", "dbt_packages"]

models:
  trivia_benchmark:
    staging:
      +materialized: view
    intermediate:
      +materialized: table
    marts:
      +materialized: table
```

`dbt_project/profiles.yml` :

```yaml
trivia_benchmark:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: "../data/gold/benchmark.duckdb"
      threads: 4
```

- [ ] **Step 2: Déclarer les sources**

`dbt_project/models/staging/sources.yml` :

```yaml
version: 2

sources:
  - name: silver
    description: Parquets produits par les étages silver du pipeline Python.
    tables:
      - name: questions
        meta:
          external_location: "read_parquet('../data/silver/questions.parquet')"
      - name: answers
        meta:
          external_location: >-
            read_parquet('../data/silver/answers/**/*.parquet', union_by_name = true)
      - name: judgments
        meta:
          external_location: >-
            read_parquet('../data/silver/judgments/**/*.parquet', union_by_name = true)
```

`hive_partitioning` reste désactivé volontairement : les chemins encodent
`model=` et `prompt_variant=`, mais ces deux valeurs sont déjà des colonnes à
l'intérieur des fichiers. L'activer ferait entrer en collision la colonne du
chemin et la colonne du fichier. `union_by_name` protège des ajouts de colonnes
entre deux runs.

- [ ] **Step 3: Écrire les modèles de staging**

`dbt_project/models/staging/stg_questions.sql` :

```sql
select
    question_id,
    category,
    category_group,
    category_name,
    type as question_type,
    difficulty,
    question,
    correct_answer,
    choices,
    correct_answer_position,
    n_choices,
    answer_is_numeric,
    question_len,
    answer_len
from {{ source('silver', 'questions') }}
```

`dbt_project/models/staging/stg_answers.sql` :

```sql
select
    question_id,
    model_slug,
    prompt_variant,
    model_name,
    prompt_hash,
    ai_answer,
    raw_answer,
    response_time,
    prompt_tokens,
    completion_tokens,
    finish_reason,
    status,
    error,
    attempt,
    is_warmup,
    answered_at,
    run_id,
    host,
    hardware
from {{ source('silver', 'answers') }}
```

`dbt_project/models/staging/stg_judgments.sql` :

```sql
select
    question_id,
    model_slug,
    prompt_variant,
    ai_answer_norm,
    match_method,
    ai_correct_strict,
    ai_correct,
    fuzzy_score,
    judge_model,
    judgment_version
from {{ source('silver', 'judgments') }}
```

- [ ] **Step 4: Écrire le modèle intermédiaire**

`dbt_project/models/intermediate/int_results.sql` :

```sql
-- Une ligne par triplet (question, modèle, variante de prompt).
-- Les lignes en erreur d'infrastructure sont conservées mais marquées :
-- elles doivent sortir du dénominateur d'accuracy, pas des volumétries.
select
    a.question_id,
    a.model_slug,
    a.model_name,
    a.prompt_variant,
    a.prompt_hash,
    q.category,
    q.category_group,
    q.category_name,
    q.question_type,
    q.difficulty,
    q.question,
    q.correct_answer,
    q.correct_answer_position,
    q.n_choices,
    q.answer_is_numeric,
    q.question_len,
    q.answer_len,
    a.ai_answer,
    a.response_time,
    a.prompt_tokens,
    a.completion_tokens,
    a.status,
    a.is_warmup,
    a.host,
    a.hardware,
    a.run_id,
    j.match_method,
    j.ai_correct_strict,
    j.ai_correct,
    j.fuzzy_score,
    a.status = 'ok' as is_scorable
from {{ ref('stg_answers') }} as a
inner join {{ ref('stg_questions') }} as q
    on a.question_id = q.question_id
left join {{ ref('stg_judgments') }} as j
    on a.question_id = j.question_id
    and a.model_slug = j.model_slug
    and a.prompt_variant = j.prompt_variant
```

- [ ] **Step 5: Déclarer les tests de staging**

`dbt_project/models/staging/schema.yml` :

```yaml
version: 2

models:
  - name: stg_questions
    columns:
      - name: question_id
        tests: [unique, not_null]
      - name: difficulty
        tests:
          - accepted_values:
              values: ["easy", "medium", "hard"]
      - name: question_type
        tests:
          - accepted_values:
              values: ["multiple", "boolean"]

  - name: stg_answers
    columns:
      - name: question_id
        tests:
          - not_null
          - relationships:
              to: ref('stg_questions')
              field: question_id
      - name: status
        tests:
          - accepted_values:
              values: ["ok", "error"]

  - name: stg_judgments
    columns:
      - name: match_method
        tests:
          - accepted_values:
              values:
                ["boolean", "exact", "choice_letter", "choice_match", "fuzzy",
                 "llm_judge", "no_match", "error"]
```

`dbt_project/models/intermediate/schema.yml` :

```yaml
version: 2

models:
  - name: int_results
    tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns: [question_id, model_slug, prompt_variant]
    columns:
      - name: question_id
        tests: [not_null]
      - name: model_slug
        tests: [not_null]
      - name: prompt_variant
        tests: [not_null]
```

Ce test de combinaison exige le package `dbt_utils`. Créer `dbt_project/packages.yml` :

```yaml
packages:
  - package: dbt-labs/dbt_utils
    version: [">=1.3.0", "<2.0.0"]
```

- [ ] **Step 6: Construire et vérifier**

Run: `cd dbt_project && dbt deps && dbt build --profiles-dir .`
Expected: tous les modèles construits, tous les tests au vert. Si `read_parquet` ne trouve pas les chemins, vérifier que la commande est bien lancée depuis `dbt_project/` : les chemins des sources sont relatifs à ce répertoire.

---

### Task 14: Marts métier

**Files:**
- Create: `dbt_project/models/marts/mart_model_performance.sql`, `mart_performance_by_category.sql`, `mart_performance_by_difficulty.sql`, `mart_prompt_performance.sql`, `mart_latency.sql`, `mart_question_hardness.sql`, `mart_matching_impact.sql`, `mart_position_bias.sql`, `mart_errors.sql`, `dbt_project/models/marts/schema.yml`

**Interfaces:**
- Consumes: `int_results`.
- Produces: neuf tables dans `benchmark.duckdb`, consommées telles quelles par le dashboard.

- [ ] **Step 1: Performance globale**

`mart_model_performance.sql` :

```sql
select
    model_slug,
    model_name,
    count(*) as n_total,
    count(*) filter (where is_scorable) as n_scorable,
    count(*) filter (where not is_scorable) as n_errors,
    avg(ai_correct_strict::int) filter (where is_scorable) as accuracy_strict,
    avg(ai_correct::int) filter (where is_scorable) as accuracy_permissive,
    median(response_time) filter (where is_scorable and not is_warmup) as response_time_median,
    quantile_cont(response_time, 0.95) filter (where is_scorable and not is_warmup) as response_time_p95,
    avg(completion_tokens) filter (where is_scorable) as completion_tokens_avg
from {{ ref('int_results') }}
group by 1, 2
```

- [ ] **Step 2: Par catégorie et par difficulté**

`mart_performance_by_category.sql` :

```sql
with per_model as (
    select model_slug, avg(ai_correct::int) as model_accuracy
    from {{ ref('int_results') }}
    where is_scorable
    group by 1
)

select
    r.model_slug,
    r.category,
    r.category_group,
    r.category_name,
    count(*) as n_questions,
    avg(r.ai_correct_strict::int) as accuracy_strict,
    avg(r.ai_correct::int) as accuracy_permissive,
    avg(r.ai_correct::int) - m.model_accuracy as delta_to_model_average
from {{ ref('int_results') }} as r
inner join per_model as m on r.model_slug = m.model_slug
where r.is_scorable
group by 1, 2, 3, 4, m.model_accuracy
```

`mart_performance_by_difficulty.sql` :

```sql
select
    model_slug,
    prompt_variant,
    difficulty,
    question_type,
    count(*) as n_questions,
    avg(ai_correct_strict::int) as accuracy_strict,
    avg(ai_correct::int) as accuracy_permissive,
    median(response_time) filter (where not is_warmup) as response_time_median
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3, 4
```

- [ ] **Step 3: Effet du prompt**

`mart_prompt_performance.sql` :

```sql
-- p1 est le mode contraint, p2 et p3 les modes ouverts. L'écart entre les deux
-- modes sépare ce que le modèle reconnaît de ce qu'il sait restituer.
select
    model_slug,
    prompt_variant,
    case when prompt_variant = 'p1_constrained_mcq' then 'constrained' else 'open' end as mode,
    count(*) as n_questions,
    avg(ai_correct_strict::int) as accuracy_strict,
    avg(ai_correct::int) as accuracy_permissive,
    avg(length(ai_answer)) as answer_len_avg,
    avg(completion_tokens) as completion_tokens_avg,
    median(response_time) filter (where not is_warmup) as response_time_median
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3
```

- [ ] **Step 4: Latence par poste**

`mart_latency.sql` :

```sql
-- Cloisonné par poste : les runs sont répartis sur des machines différentes,
-- comparer des temps entre matériels ne mesure rien.
select
    model_slug,
    host,
    hardware,
    prompt_variant,
    count(*) as n_questions,
    avg(response_time) as response_time_avg,
    median(response_time) as response_time_median,
    quantile_cont(response_time, 0.95) as response_time_p95,
    sum(completion_tokens) / nullif(sum(response_time), 0) as tokens_per_second
from {{ ref('int_results') }}
where is_scorable and not is_warmup
group by 1, 2, 3, 4
```

- [ ] **Step 5: Difficulté réelle des questions**

`mart_question_hardness.sql` :

```sql
select
    question_id,
    question,
    category,
    difficulty,
    question_type,
    correct_answer,
    count(*) as n_attempts,
    sum(ai_correct::int) as n_correct,
    avg(ai_correct::int) as success_rate,
    count(*) = sum((not ai_correct)::int) as failed_by_everyone
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3, 4, 5, 6
```

- [ ] **Step 6: Impact du matching et biais de position**

`mart_matching_impact.sql` :

```sql
select
    model_slug,
    prompt_variant,
    match_method,
    count(*) as n_answers,
    count(*) * 1.0 / sum(count(*)) over (partition by model_slug, prompt_variant) as share,
    avg(ai_correct::int) as accuracy_permissive,
    avg(ai_correct_strict::int) as accuracy_strict,
    avg(fuzzy_score) as fuzzy_score_avg
from {{ ref('int_results') }}
where is_scorable
group by 1, 2, 3
```

`mart_position_bias.sql` :

```sql
-- Uniquement en mode contraint : c'est le seul où le modèle voit les options.
-- On compare la position choisie à la position réellement correcte.
with chosen as (
    select
        r.model_slug,
        r.question_id,
        r.correct_answer_position,
        r.n_choices,
        list_position(q.choices, r.ai_answer) - 1 as chosen_position
    from {{ ref('int_results') }} as r
    inner join {{ ref('stg_questions') }} as q on r.question_id = q.question_id
    where r.prompt_variant = 'p1_constrained_mcq' and r.is_scorable
)

select
    model_slug,
    n_choices,
    chosen_position,
    count(*) as n_chosen,
    count(*) filter (where chosen_position = correct_answer_position) as n_correct_at_position,
    count(*) * 1.0 / sum(count(*)) over (partition by model_slug, n_choices) as chosen_share
from chosen
where chosen_position >= 0
group by 1, 2, 3
```

- [ ] **Step 7: Erreurs d'infrastructure**

`mart_errors.sql` :

```sql
select
    model_slug,
    prompt_variant,
    host,
    run_id,
    count(*) as n_errors,
    min(answered_at) as first_error_at,
    max(answered_at) as last_error_at
from {{ ref('int_results') }}
where not is_scorable
group by 1, 2, 3, 4
```

- [ ] **Step 8: Tests métier**

`dbt_project/models/marts/schema.yml` :

```yaml
version: 2

models:
  - name: mart_model_performance
    columns:
      - name: model_slug
        tests: [unique, not_null]
      - name: accuracy_permissive
        tests:
          - dbt_utils.accepted_range:
              min_value: 0
              max_value: 1

  - name: mart_prompt_performance
    tests:
      - dbt_utils.unique_combination_of_columns:
          combination_of_columns: [model_slug, prompt_variant]

  - name: mart_matching_impact
    columns:
      - name: match_method
        tests: [not_null]
```

Ajouter le test générique `permissive_at_least_strict` dans `dbt_project/tests/permissive_at_least_strict.sql` :

```sql
-- Le taux permissif inclut le strict par construction : il ne peut lui être
-- inférieur. Une violation signale une régression dans la cascade.
select model_slug, prompt_variant, accuracy_strict, accuracy_permissive
from {{ ref('mart_prompt_performance') }}
where accuracy_permissive < accuracy_strict
```

- [ ] **Step 9: Construire et vérifier**

Run: `cd dbt_project && dbt build --profiles-dir .`
Expected: neuf marts construits, tous les tests au vert, y compris `permissive_at_least_strict`.

---

### Task 15: Dashboard Streamlit

**Files:**
- Create: `app/streamlit_app.py`
- Create: `app/queries.py`
- Test: `tests/test_queries.py`

**Interfaces:**
- Consumes: `data/gold/benchmark.duckdb`.
- Produces: `app.queries.connect(path) -> duckdb.DuckDBPyConnection`, `app.queries.load(conn, mart_name: str) -> pandas.DataFrame`, `app.queries.available_marts(conn) -> list[str]`, `app.queries.MARTS: tuple[str, ...]`.

- [ ] **Step 1: Écrire les tests de la couche d'accès**

Créer `tests/test_queries.py` :

```python
import duckdb
import pandas as pd
import pytest

from app.queries import MARTS, available_marts, connect, load


@pytest.fixture
def gold(tmp_path):
    path = tmp_path / "benchmark.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute(
        "create table mart_model_performance as "
        "select 'm1' as model_slug, 0.5 as accuracy_strict, 0.6 as accuracy_permissive"
    )
    conn.close()
    return path


def test_connect_is_read_only(gold):
    conn = connect(gold)
    with pytest.raises(duckdb.Error):
        conn.execute("create table t as select 1")


def test_load_returns_dataframe(gold):
    frame = load(connect(gold), "mart_model_performance")
    assert isinstance(frame, pd.DataFrame)
    assert frame.loc[0, "model_slug"] == "m1"


def test_load_rejects_unknown_table(gold):
    with pytest.raises(ValueError):
        load(connect(gold), "drop table users")


def test_available_marts_lists_only_existing_tables(gold):
    assert available_marts(connect(gold)) == ["mart_model_performance"]


def test_missing_database_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        connect(tmp_path / "absent.duckdb")


def test_mart_catalogue_is_complete():
    assert len(MARTS) == 9
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_queries.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.queries'`.

- [ ] **Step 3: Implémenter la couche d'accès**

Créer `app/__init__.py` vide, puis `app/queries.py` :

```python
"""Accès en lecture seule au DuckDB gold.

Le nom de table est validé contre un catalogue fermé : une interpolation de
chaîne dans une requête, même dans une application locale, reste une injection.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

MARTS = (
    "mart_model_performance",
    "mart_performance_by_category",
    "mart_performance_by_difficulty",
    "mart_prompt_performance",
    "mart_latency",
    "mart_question_hardness",
    "mart_matching_impact",
    "mart_position_bias",
    "mart_errors",
)


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Base gold introuvable: {path}. Lancez `cd dbt_project && dbt build --profiles-dir .`"
        )
    return duckdb.connect(str(path), read_only=True)


def load(conn: duckdb.DuckDBPyConnection, mart_name: str) -> pd.DataFrame:
    if mart_name not in MARTS:
        raise ValueError(f"Table inconnue: {mart_name!r}. Connues: {list(MARTS)}")
    return conn.execute(f"select * from {mart_name}").fetch_df()


def available_marts(conn: duckdb.DuckDBPyConnection) -> list[str]:
    existing = {row[0] for row in conn.execute("show tables").fetchall()}
    return [name for name in MARTS if name in existing]
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_queries.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Écrire le dashboard**

Créer `app/streamlit_app.py` :

```python
"""Rapport interactif du benchmark."""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from app.queries import available_marts, connect, load

st.set_page_config(page_title="Benchmark LLM — OpenTDB", layout="wide")


@st.cache_resource
def get_connection():
    return connect(config.GOLD_DUCKDB)


@st.cache_data
def get_mart(name: str):
    return load(get_connection(), name)


def percent_axis(field: str, title: str) -> alt.Y:
    return alt.Y(field, title=title, axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1]))


try:
    connection = get_connection()
except FileNotFoundError as exc:
    st.error(str(exc))
    st.stop()

marts = available_marts(connection)
PAGES = [
    "Vue d'ensemble",
    "Catégories",
    "Difficulté",
    "Prompts",
    "Latence",
    "Qualité du matching",
    "Explorateur",
]
page = st.sidebar.radio("Page", PAGES)
st.sidebar.caption(f"{len(marts)} tables disponibles dans {config.GOLD_DUCKDB.name}")

if page == "Vue d'ensemble":
    st.title("Performance globale")
    frame = get_mart("mart_model_performance")
    columns = st.columns(len(frame))
    for column, row in zip(columns, frame.to_dict(orient="records")):
        column.metric(
            row["model_name"],
            f"{row['accuracy_permissive']:.1%}",
            delta=f"strict {row['accuracy_strict']:.1%}",
        )
    melted = frame.melt(
        id_vars=["model_slug"],
        value_vars=["accuracy_strict", "accuracy_permissive"],
        var_name="mesure",
        value_name="taux",
    )
    st.altair_chart(
        alt.Chart(melted)
        .mark_bar()
        .encode(x="model_slug:N", y=percent_axis("taux:Q", "Taux de réussite"),
                color="mesure:N", xOffset="mesure:N"),
        use_container_width=True,
    )
    errors = get_mart("mart_errors")
    if len(errors):
        st.warning(f"{int(errors['n_errors'].sum())} appels en erreur, exclus du dénominateur.")
    st.dataframe(frame, use_container_width=True)

elif page == "Catégories":
    st.title("Précision par catégorie")
    frame = get_mart("mart_performance_by_category")
    st.altair_chart(
        alt.Chart(frame)
        .mark_rect()
        .encode(
            x="model_slug:N",
            y=alt.Y("category:N", sort="-x"),
            color=alt.Color("accuracy_permissive:Q", title="Taux", scale=alt.Scale(scheme="viridis")),
            tooltip=["category", "model_slug", "accuracy_permissive", "n_questions"],
        )
        .properties(height=600),
        use_container_width=True,
    )
    st.dataframe(frame.sort_values("delta_to_model_average"), use_container_width=True)

elif page == "Difficulté":
    st.title("Précision par niveau de difficulté")
    frame = get_mart("mart_performance_by_difficulty")
    st.altair_chart(
        alt.Chart(frame)
        .mark_line(point=True)
        .encode(
            x=alt.X("difficulty:N", sort=["easy", "medium", "hard"]),
            y=percent_axis("accuracy_permissive:Q", "Taux de réussite"),
            color="model_slug:N",
            strokeDash="question_type:N",
            tooltip=["model_slug", "prompt_variant", "difficulty", "n_questions"],
        ),
        use_container_width=True,
    )
    st.dataframe(frame, use_container_width=True)

elif page == "Prompts":
    st.title("Effet de la variante de prompt")
    frame = get_mart("mart_prompt_performance")
    st.altair_chart(
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x="prompt_variant:N",
            y=percent_axis("accuracy_permissive:Q", "Taux de réussite"),
            color="mode:N",
            column="model_slug:N",
            tooltip=["n_questions", "answer_len_avg", "completion_tokens_avg"],
        ),
        use_container_width=True,
    )
    st.caption(
        "Le mode contraint fournit les options au modèle : il mesure la reconnaissance. "
        "Les modes ouverts posent la question nue : ils mesurent la restitution."
    )
    st.dataframe(frame, use_container_width=True)

elif page == "Latence":
    st.title("Temps de réponse")
    st.info(
        "Les runs sont répartis sur plusieurs postes. Les temps ne sont comparables "
        "qu'à l'intérieur d'un même poste."
    )
    frame = get_mart("mart_latency")
    host = st.selectbox("Poste", sorted(frame["host"].unique()))
    subset = frame[frame["host"] == host]
    st.caption(f"Matériel : {subset['hardware'].iloc[0]}")
    st.altair_chart(
        alt.Chart(subset)
        .mark_bar()
        .encode(x="model_slug:N", y="response_time_median:Q", color="prompt_variant:N",
                xOffset="prompt_variant:N"),
        use_container_width=True,
    )
    st.dataframe(subset, use_container_width=True)

elif page == "Qualité du matching":
    st.title("Comment les verdicts sont rendus")
    frame = get_mart("mart_matching_impact")
    st.altair_chart(
        alt.Chart(frame)
        .mark_bar()
        .encode(x=alt.X("share:Q", axis=alt.Axis(format="%")), y="model_slug:N",
                color="match_method:N", tooltip=["match_method", "n_answers", "share"]),
        use_container_width=True,
    )
    st.dataframe(frame, use_container_width=True)
    if "mart_position_bias" in marts:
        st.subheader("Biais de position en QCM contraint")
        st.altair_chart(
            alt.Chart(get_mart("mart_position_bias"))
            .mark_bar()
            .encode(x="chosen_position:O", y=alt.Y("chosen_share:Q", axis=alt.Axis(format="%")),
                    color="model_slug:N", column="n_choices:O"),
            use_container_width=True,
        )
        st.caption(
            "Les options sont mélangées avec un ordre seedé par question : une distribution "
            "non uniforme révèle une préférence de position du modèle, pas un artefact du dataset."
        )

else:
    st.title("Explorateur de questions")
    frame = get_mart("mart_question_hardness")
    only_failed = st.checkbox("Uniquement les questions ratées par tous les modèles")
    categories = st.multiselect("Catégories", sorted(frame["category"].unique()))
    view = frame
    if only_failed:
        view = view[view["failed_by_everyone"]]
    if categories:
        view = view[view["category"].isin(categories)]
    st.caption(f"{len(view)} questions")
    st.dataframe(view.sort_values("success_rate"), use_container_width=True)
```

- [ ] **Step 6: Lancer le dashboard**

Run: `streamlit run app/streamlit_app.py`
Expected: les sept pages s'affichent sans erreur. Si une page est vide, vérifier que le mart correspondant existe avec `dbt build`.

---

### Task 16: README et exclusion des documents de travail

**Files:**
- Modify: `README.md` (réécriture complète)
- Modify: `.gitignore`

- [ ] **Step 1: Exclure les documents de travail du dépôt**

Ajouter à la fin de `.gitignore` :

```
# Artefacts dbt et données générées
dbt_project/target/
dbt_project/dbt_packages/
dbt_project/logs/
data/gold/*
!data/gold/.gitkeep
```

Les lignes `docs/superpowers/` et `.superpowers/` sont déjà présentes dans le
fichier : ne pas les redoubler.

Puis créer le répertoire gold suivi : `mkdir -p data/gold && touch data/gold/.gitkeep`

- [ ] **Step 2: Réécrire le README**

```markdown
# Trivial Poursuite — Benchmark LLM sur OpenTDB

Pipeline de data engineering évaluant plusieurs modèles de langage locaux sur
l'intégralité du corpus Open Trivia Database, avec trois variantes de prompt.

## Architecture médaillon

| Couche | Emplacement | Contenu |
| --- | --- | --- |
| Bronze | `data/bronze/questions_raw.csv` | Questions brutes OpenTDB, identifiant stable, payloads d'API archivés |
| Silver | `data/silver/questions.parquet` | Questions nettoyées, options mélangées avec un ordre seedé |
| Silver | `data/silver/answers/model=…/prompt_variant=…/` | Réponses des modèles, temps de réponse, provenance |
| Silver | `data/silver/judgments/model=…/prompt_variant=…/` | Verdicts et méthode de décision |
| Gold | `data/gold/benchmark.duckdb` | Neuf marts métier construits par dbt |
| Restitution | `app/streamlit_app.py` | Rapport interactif |

Chaque étage écrit un artefact immuable et ne relit que l'étage précédent.
Le jugement est séparé de l'inférence : ajuster un seuil de comparaison se rejoue
en minutes, sans refaire des heures d'appels au modèle.

## Prérequis

- Python 3.10 ou plus
- LM Studio, serveur local démarré (onglet Developer), modèle chargé en mémoire
- Environ 16 Go de RAM libre pour un modèle 27B en quantization 4 bits

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # renseigner LLM_MODEL
```

## Exécution

```bash
# Pipeline complet
python run_pipeline.py

# Étages séparés
python run_pipeline.py --stages ingest transform
python run_pipeline.py --stages enrich --model "google/gemma-4-12b-qat"
python run_pipeline.py --stages judge

# Répartition du travail sur trois postes
python run_pipeline.py --stages enrich --shard 0/3    # poste 1
python run_pipeline.py --stages enrich --shard 1/3    # poste 2
python run_pipeline.py --stages enrich --shard 2/3    # poste 3

# Smoke test rapide
python run_pipeline.py --stages enrich --sample-size 20 --variants p1_constrained_mcq
```

Couche gold et dashboard :

```bash
cd dbt_project && dbt deps && dbt build --profiles-dir . && cd ..
streamlit run app/streamlit_app.py
```

L'inférence est reprenable : relancer la même commande après une interruption
reprend là où le run s'est arrêté, sans réinterroger le modèle sur ce qui est
déjà fait.

## Méthodologie

**Collecte.** L'API OpenTDB sert au maximum 50 questions par appel, pour une
seule catégorie, à raison d'une requête toutes les 5 secondes. Elle expose
5 298 questions vérifiées sur 24 catégories ; les questions en attente et
rejetées ne sont pas accessibles. Le session token est le seul mécanisme
anti-doublons. Piège principal : le code de réponse 1 est renvoyé sans aucune
question quand la catégorie contient moins de questions non servies que le
nombre demandé. Le scraper dégrade donc le montant demandé selon la séquence
50, 25, 10, 5, 1 avant de conclure à l'épuisement, ce qui récupère la queue de
chaque catégorie. Les champs sont demandés en base64 pour éviter les entités
HTML doublement encodées.

**Identifiant.** `question_id` est un SHA-256 tronqué du couple question plus
réponse correcte, normalisé de façon minimale et calculé dès l'ingestion. Il ne
dépend d'aucune décision de nettoyage ultérieure : faire évoluer le silver ne
rend jamais orphelines les réponses déjà obtenues.

**Ordre des options.** Les propositions sont mélangées par un générateur seedé
avec `question_id`. Un tri alphabétique plaçait systématiquement `False` en
première position sur les questions vrai/faux et ordonnait les réponses
numériques par magnitude ; le biais de position des modèles devenait alors
corrélé au contenu, donc inégal selon la catégorie. Le mélange est reproductible
à l'identique entre postes et entre exécutions.

**Variantes de prompt.** Trois variantes standardisées, tracées par identifiant
et par empreinte dans chaque ligne de résultat. `p1_constrained_mcq` fournit les
options et demande une recopie mot pour mot : elle mesure la reconnaissance.
`p2_open_minimal` pose la question nue avec une consigne minimale.
`p3_open_guided` pose la question nue avec une consigne de format renforcée.
L'écart entre les modes sépare ce qu'un modèle reconnaît de ce qu'il sait
restituer ; l'écart entre les deux variantes ouvertes isole l'apport du prompt.

**Décision de justesse.** Une cascade à niveaux rend chaque verdict auditable :
comparaison booléenne, égalité après normalisation, résolution d'une réponse
donnée par lettre ou par rang, correspondance avec une option unique,
comparaison approchée par `token_set_ratio`, puis arbitrage par le modèle local
en dernier recours. La comparaison approchée est désactivée sur les réponses
numériques, où « 1789 » et « 1798 » obtiennent un score de similarité élevé sans
être équivalents. La méthode retenue est stockée dans `match_method`, et deux
taux sont publiés : strict, limité aux correspondances exactes, et permissif,
incluant les niveaux approchés.

**Mesure du temps.** Les appels sont séquentiels : paralléliser rendrait
`response_time` ininterprétable, LM Studio mettant les requêtes en file. La
première inférence de chaque run porte le chargement du modèle et est marquée
`is_warmup`. Chaque ligne enregistre le poste et le matériel : les runs étant
répartis entre les machines de l'équipe, les temps ne sont comparables qu'à
l'intérieur d'un même poste, ce que le dashboard applique.

**Erreurs.** Un échec d'appel n'est pas une mauvaise réponse. La ligne est
conservée avec `status = error`, exclue du dénominateur des taux de réussite, et
repasse dans la file au run suivant.

## Organisation du projet

| Fichier | Rôle |
| --- | --- |
| `config.py` | Chemins et paramètres, modèle lu depuis l'environnement |
| `src/opentdb_client.py` | Transport HTTP : rythme, retries, codes de réponse |
| `src/ingest_opentdb.py` | Collecte par catégorie vers la couche bronze |
| `src/transform_silver.py` | Nettoyage et mélange seedé des options |
| `src/prompts.py` | Registre versionné des trois variantes |
| `src/llm_client.py` | Appel au runtime local, chronométrage, tokens |
| `src/enrich_llm.py` | Inférence reprenable, écriture partitionnée |
| `src/scoring.py` | Primitives de comparaison |
| `src/judge.py` | Cascade de jugement |
| `src/io_utils.py`, `src/runmeta.py` | Écritures atomiques, provenance |
| `dbt_project/` | Staging, intermédiaire, neuf marts, tests |
| `app/` | Dashboard Streamlit |
| `tests/` | Suite pytest, sans accès réseau ni modèle |

## Tests

```bash
python -m pytest -v
cd dbt_project && dbt test --profiles-dir .
```
```

- [ ] **Step 3: Vérifier la suite complète**

Run: `python -m pytest -v`
Expected: PASS, toute la suite.

Run: `git status --short`
Expected: aucun fichier sous `docs/superpowers/` ni `data/` n'apparaît comme non suivi.

---

## Commits

Un commit par tâche, directement sur `main`, réalisé par l'agent qui exécute la
tâche après passage des tests. Aucun push : la vérification finale est faite en
fin de plan, avant tout envoi vers le dépôt distant.

Format du message, sans rien après la dernière ligne de contenu :

| Tâche | Message |
| --- | --- |
| 1 | `chore: configuration pilotée par l'environnement et socle pytest` |
| 2 | `feat: écritures atomiques pour les artefacts de données` |
| 3 | `feat: métadonnées de provenance des runs` |
| 4 | `feat: client OpenTDB avec rythme partagé et décodage base64` |
| 5 | `fix: récupérer la queue de chaque catégorie OpenTDB` |
| 6 | `fix: mélange seedé des options et schéma silver enrichi` |
| 7 | `feat: registre versionné des trois variantes de prompt` |
| 8 | `feat: wrapper LM Studio chronométré` |
| 9 | `fix: inférence multi-modèles reprenable et partitionnée` |
| 10 | `fix: primitives de comparaison sans faux positifs` |
| 11 | `feat: cascade de jugement traçable` |
| 12 | `feat: orchestration par étages avec sharding` |
| 13 | `feat: projet dbt, staging et modèle intermédiaire` |
| 14 | `feat: neuf marts métier et tests dbt` |
| 15 | `feat: dashboard Streamlit du benchmark` |
| 16 | `docs: méthodologie et setup complets` |

Contrôles avant chaque commit :

- `python -m pytest` passe
- `git status --short` ne montre ni `docs/superpowers/` ni fichier sous `data/`
- le message ne contient aucune mention d'outil ni de coauteur

## Self-review

Couverture de la spec, section par section :

| Section de la spec | Tâches |
| --- | --- |
| §4 Collecte, pièges et schéma bronze | 4, 5 |
| §5 Nettoyage silver et mélange seedé | 6 |
| §6 Trois variantes de prompt | 7 |
| §7 Inférence, reprise, provenance | 3, 8, 9 |
| §8 Cascade de jugement | 10, 11 |
| §9 Couche gold dbt | 13, 14 |
| §10 Dashboard sept pages | 15 |
| §11 Plan de tests | présent dans chaque tâche |
| §12 Les 19 correctifs | 1 (14), 2 (10), 4 (11), 5 (1, 7, 10, 12, 13), 6 (6), 8 (8, 18), 9 (2, 3, 4, 5, 9, 17, 19), 11 (19), 16 (15, 16) |
| §13 Répartition à trois | frontières de modules respectées, shard implémenté en tâche 9 |

Les 19 correctifs de la spec sont couverts. Le correctif 9, compteurs de tokens,
n'est acquis qu'après la vérification manuelle de l'étape 5 de la tâche 8 : les
noms de champs du SDK LM Studio doivent être confirmés sur la version installée.
