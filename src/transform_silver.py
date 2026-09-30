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
    """Ordre reproductible et décorrélé du contenu.

    Les propositions en doublon exact sont écartées : OpenTDB contient des
    lignes où une mauvaise réponse reprend mot pour mot la bonne, ce qui
    rendrait `correct_answer_position` ambigu.
    """
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
