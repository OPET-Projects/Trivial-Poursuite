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
