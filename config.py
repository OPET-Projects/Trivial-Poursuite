"""Paramètres globaux du pipeline Trivial Pursuit."""

from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"

BRONZE_CSV = BRONZE_DIR / "questions_raw.csv"
INGEST_CHECKPOINT = BRONZE_DIR / "ingest_checkpoint.json"
SILVER_CLEAN = SILVER_DIR / "questions_clean.parquet"
SILVER_ENRICHED = SILVER_DIR / "questions_enriched.parquet"

# OpenTDB
OPENTDB_BASE = "https://opentdb.com"
OPENTDB_BATCH_SIZE = 50
RATE_LIMIT_SECONDS = 5.1
HTTP_TIMEOUT_SECONDS = 30
MAX_RETRIES = 8

# Échantillon LLM
SAMPLE_SIZE = 400
SAMPLE_SEED = 42

# LM Studio
MODEL_NAME = "google/gemma-4-12b-qat"
LMSTUDIO_TIMEOUT_SECONDS = 180
LLM_TEMPERATURE = 0
LLM_MAX_TOKENS = 64

# Prompt standardisé (tracé dans le dataset)
PROMPT_ID = "strict_verbatim_v1"
SYSTEM_PROMPT = (
    "You are a trivia answering engine. "
    "Reply with the answer only. "
    "No explanation, no extra punctuation, no markdown, no quotes. "
    "If the question is multiple choice, copy one of the given options verbatim. "
    "If the question is true/false, reply with True or False only."
)

# Scoring
FUZZY_RATIO_THRESHOLD = 90
