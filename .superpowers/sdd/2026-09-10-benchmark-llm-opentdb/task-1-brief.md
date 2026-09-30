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
    reloaded = importlib.reload(config)
    assert reloaded.MODEL_NAME == "some/other-model"
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

