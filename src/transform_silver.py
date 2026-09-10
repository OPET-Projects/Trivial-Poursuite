"""Nettoyage bronze → silver/questions_clean.parquet."""

from __future__ import annotations

import ast
import hashlib
import html
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config


def _unescape(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return html.unescape(str(value)).strip()


def _parse_incorrect(value: object) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return [_unescape(item) for item in value]
    text = str(value).strip()
    if not text:
        return []
    parsed: object
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = ast.literal_eval(text)
    if not isinstance(parsed, list):
        return [_unescape(parsed)]
    return [_unescape(item) for item in parsed]


def make_question_id(category: str, question: str, correct_answer: str) -> str:
    payload = f"{category}\n{question}\n{correct_answer}".encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def _all_answers(correct: str, incorrect: list[str]) -> list[str]:
    unique = []
    seen: set[str] = set()
    for answer in [correct, *incorrect]:
        if answer and answer not in seen:
            unique.append(answer)
            seen.add(answer)
    return sorted(unique, key=lambda item: item.casefold())


def clean_questions(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = pd.DataFrame(
        {
            "category": df["category"].map(_unescape),
            "type": df["type"].map(lambda v: _unescape(v).lower()),
            "difficulty": df["difficulty"].map(lambda v: _unescape(v).lower()),
            "question": df["question"].map(_unescape),
            "correct_answer": df["correct_answer"].map(_unescape),
            "incorrect_answers": df["incorrect_answers"].map(_parse_incorrect),
        }
    )
    cleaned = cleaned[cleaned["question"].str.len() > 0]
    cleaned = cleaned[cleaned["correct_answer"].str.len() > 0]
    cleaned["question_id"] = [
        make_question_id(row.category, row.question, row.correct_answer)
        for row in cleaned.itertuples(index=False)
    ]
    cleaned["all_answers"] = [
        _all_answers(row.correct_answer, row.incorrect_answers)
        for row in cleaned.itertuples(index=False)
    ]
    cleaned = cleaned.drop_duplicates(subset=["question_id"]).reset_index(drop=True)
    return cleaned[
        [
            "question_id",
            "category",
            "type",
            "difficulty",
            "question",
            "correct_answer",
            "incorrect_answers",
            "all_answers",
        ]
    ]


def run_transform() -> Path:
    if not config.BRONZE_CSV.exists():
        raise FileNotFoundError(
            f"Couche bronze introuvable: {config.BRONZE_CSV}. Lancez d'abord l'ingest."
        )
    config.SILVER_DIR.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(config.BRONZE_CSV)
    cleaned = clean_questions(raw)
    cleaned.to_parquet(config.SILVER_CLEAN, index=False)
    print(
        f"[silver] {len(raw)} brutes → {len(cleaned)} propres "
        f"({len(raw) - len(cleaned)} doublons/vides retirés) → {config.SILVER_CLEAN}"
    )
    return config.SILVER_CLEAN


def main() -> None:
    run_transform()


if __name__ == "__main__":
    main()
