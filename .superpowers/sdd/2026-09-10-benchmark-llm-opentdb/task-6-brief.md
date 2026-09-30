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
_NUMERIC = re.compile(r"^[\d\s.,%/+-]+$")

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
    choices = [correct, *incorrect]
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

