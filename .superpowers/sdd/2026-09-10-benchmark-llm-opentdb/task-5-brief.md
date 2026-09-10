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

        if code == ResponseCode.TOKEN_EMPTY:
            break

        if code == ResponseCode.NO_RESULTS:
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

