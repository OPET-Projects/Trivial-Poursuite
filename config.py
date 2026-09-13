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
MODEL_NAME = os.environ.get("LLM_MODEL", "google/gemma-4-26b-a4b-qat")
JUDGE_MODEL_NAME = os.environ.get("JUDGE_MODEL", MODEL_NAME)
LMSTUDIO_BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234").rstrip("/")
LMSTUDIO_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "180"))
# "none" aligne les modèles à raisonnement sur le protocole des modèles qui
# n'en ont pas ; toute autre valeur rend les runs incomparables entre eux.
LLM_REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "none")
LLM_TEMPERATURE = 0.0
LLM_MAX_TOKENS = 256
LLM_MAX_ATTEMPTS = 3

# Écriture partitionnée
FLUSH_EVERY = 150

# Scoring
FUZZY_RATIO_THRESHOLD = 90
JUDGMENT_VERSION = "cascade_v1"
