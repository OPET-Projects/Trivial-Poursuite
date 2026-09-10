"""Collecte intégrale OpenTDB → bronze/questions_raw.csv.

OpenTDB n'a pas de pagination `offset` ni de clé API publique. Sans session
token, chaque lot de 50 est tiré au hasard et les doublons sont fréquents.

Stratégie :
- un session token (anti-doublons côté API, valable 6 h)
- parcours catégorie par catégorie jusqu'à épuisement (response_code 4/1)
- dédoublonnage local par empreinte question + réponse
- `offset` client dans le checkpoint pour reprendre ; envoyé à l'API si une
  clé `OPENTDB_API_KEY` est fournie (paramètre non documenté, ignoré sinon)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

RESPONSE_SUCCESS = 0
RESPONSE_NO_RESULTS = 1
RESPONSE_INVALID_PARAMETER = 2
RESPONSE_TOKEN_NOT_FOUND = 3
RESPONSE_TOKEN_EMPTY = 4
RESPONSE_RATE_LIMIT = 5

# L'API publique ignore souvent offset/apiKey (code 2). On ne les renvoie plus ensuite.
_USE_OFFSET_AND_KEY = True


def _ensure_dirs() -> None:
    config.BRONZE_DIR.mkdir(parents=True, exist_ok=True)


def _load_checkpoint() -> dict[str, Any]:
    if not config.INGEST_CHECKPOINT.exists():
        return {}
    return json.loads(config.INGEST_CHECKPOINT.read_text(encoding="utf-8"))


def _save_checkpoint(payload: dict[str, Any]) -> None:
    config.INGEST_CHECKPOINT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_existing_rows() -> list[dict[str, Any]]:
    if not config.BRONZE_CSV.exists():
        return []
    df = pd.read_csv(config.BRONZE_CSV)
    return df.to_dict(orient="records")


def _write_rows(rows: list[dict[str, Any]]) -> None:
    df = pd.DataFrame(rows)
    df.to_csv(config.BRONZE_CSV, index=False)


def _fingerprint(item: dict[str, Any]) -> str:
    payload = (
        f"{item.get('category', '')}\n"
        f"{item.get('question', '')}\n"
        f"{item.get('correct_answer', '')}"
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _serialize_raw(item: dict[str, Any]) -> dict[str, Any]:
    incorrect = item.get("incorrect_answers") or []
    if not isinstance(incorrect, str):
        incorrect = json.dumps(incorrect, ensure_ascii=False)
    return {
        "category": item.get("category"),
        "type": item.get("type"),
        "difficulty": item.get("difficulty"),
        "question": item.get("question"),
        "correct_answer": item.get("correct_answer"),
        "incorrect_answers": incorrect,
    }


def _get_json(session: requests.Session, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            response = session.get(url, params=params, timeout=config.HTTP_TIMEOUT_SECONDS)
            if response.status_code == 429:
                wait = config.RATE_LIMIT_SECONDS * attempt
                print(f"[ingest] rate limit HTTP 429, pause {wait:.1f}s")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            last_error = exc
            wait = config.RATE_LIMIT_SECONDS * attempt
            print(f"[ingest] tentative {attempt}/{config.MAX_RETRIES} échouée ({exc}); retry dans {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"Échec HTTP après {config.MAX_RETRIES} tentatives: {url}") from last_error


def request_token(session: requests.Session) -> str:
    payload = _get_json(
        session,
        f"{config.OPENTDB_BASE}/api_token.php",
        params={"command": "request"},
    )
    token = payload.get("token")
    if not token:
        raise RuntimeError(f"Impossible d'obtenir un token OpenTDB: {payload}")
    return token


def fetch_global_count(session: requests.Session) -> int | None:
    try:
        payload = _get_json(session, f"{config.OPENTDB_BASE}/api_count_global.php")
        overall = payload.get("overall") or {}
        return int(overall.get("total_num_of_verified_questions") or overall.get("total_num_of_questions") or 0)
    except (RuntimeError, TypeError, ValueError):
        return None


def fetch_categories(session: requests.Session) -> list[dict[str, Any]]:
    payload = _get_json(session, f"{config.OPENTDB_BASE}/api_category.php")
    categories = payload.get("trivia_categories") or []
    if not categories:
        raise RuntimeError(f"Liste de catégories vide: {payload}")
    return categories


def fetch_batch(
    session: requests.Session,
    token: str,
    *,
    category_id: int | None = None,
    offset: int | None = None,
    api_key: str | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    global _USE_OFFSET_AND_KEY
    params: dict[str, Any] = {
        "amount": config.OPENTDB_BATCH_SIZE,
        "token": token,
    }
    if category_id is not None:
        params["category"] = category_id
    if _USE_OFFSET_AND_KEY and api_key:
        if offset is not None:
            params["offset"] = offset
        params["apiKey"] = api_key

    payload = _get_json(session, f"{config.OPENTDB_BASE}/api.php", params=params)
    code = int(payload.get("response_code", -1))

    # Paramètre apiKey/offset refusé → on retente sans, le session token suffit.
    if code == RESPONSE_INVALID_PARAMETER and ("apiKey" in params or "offset" in params):
        print("[ingest] offset/apiKey non supportés par l'API, fallback session token.")
        _USE_OFFSET_AND_KEY = False
        params.pop("apiKey", None)
        params.pop("offset", None)
        time.sleep(config.RATE_LIMIT_SECONDS)
        payload = _get_json(session, f"{config.OPENTDB_BASE}/api.php", params=params)
        code = int(payload.get("response_code", -1))

    results = payload.get("results") or []
    return code, results


def _pace() -> None:
    time.sleep(config.RATE_LIMIT_SECONDS)


def run_ingest(*, force: bool = False) -> Path:
    """Scrape l'intégralité d'OpenTDB vers questions_raw.csv."""
    _ensure_dirs()
    checkpoint = _load_checkpoint()
    api_key = os.environ.get("OPENTDB_API_KEY", "").strip() or None

    if (
        not force
        and checkpoint.get("status") == "complete"
        and config.BRONZE_CSV.exists()
    ):
        n_rows = len(pd.read_csv(config.BRONZE_CSV))
        print(f"[ingest] Bronze déjà complet ({n_rows} questions). Utilisez --force-ingest pour relancer.")
        return config.BRONZE_CSV

    session = requests.Session()
    session.headers.update({"User-Agent": "trivial-pursuit-benchmark/1.0"})

    expected = fetch_global_count(session)
    _pace()
    if expected:
        print(f"[ingest] Questions vérifiées OpenTDB (indicatif) : {expected}")
    if api_key:
        print("[ingest] OPENTDB_API_KEY détectée : offset + apiKey ajoutés aux requêtes.")
    else:
        print("[ingest] Pas de clé API : session token + parcours par catégorie (anti-doublons).")

    categories = fetch_categories(session)
    _pace()

    rows: list[dict[str, Any]] = [] if force else _load_existing_rows()
    seen = {_fingerprint(row) for row in rows}
    duplicates_skipped = int(checkpoint.get("n_duplicates_skipped") or 0) if not force else 0

    token = None if force else checkpoint.get("token")
    if not token:
        token = request_token(session)
        _pace()

    start_index = 0 if force else int(checkpoint.get("last_category_index") or 0)
    offset = 0 if force else int(checkpoint.get("offset") or 0)

    print(
        f"[ingest] {len(rows)} uniques déjà en bronze, "
        f"{len(categories)} catégories, reprise à l'index {start_index}."
    )
    pbar = tqdm(total=expected or None, initial=len(rows), unit="q", desc="OpenTDB")

    try:
        for cat_index, category in enumerate(categories):
            if cat_index < start_index:
                continue
            cat_id = int(category["id"])
            cat_name = category.get("name", cat_id)
            pbar.set_postfix(cat=str(cat_name)[:24], n=len(rows))

            empty_streak = 0
            while True:
                code, results = fetch_batch(
                    session,
                    token,
                    category_id=cat_id,
                    offset=offset,
                    api_key=api_key,
                )

                if code == RESPONSE_TOKEN_NOT_FOUND:
                    token = request_token(session)
                    _pace()
                    continue

                if code == RESPONSE_RATE_LIMIT:
                    time.sleep(config.RATE_LIMIT_SECONDS * 2)
                    continue

                if code in (RESPONSE_TOKEN_EMPTY, RESPONSE_NO_RESULTS):
                    break

                if code != RESPONSE_SUCCESS:
                    _pace()
                    continue

                new_this_batch = 0
                for item in results:
                    raw = _serialize_raw(item)
                    key = _fingerprint(raw)
                    if key in seen:
                        duplicates_skipped += 1
                        continue
                    seen.add(key)
                    rows.append(raw)
                    new_this_batch += 1

                offset += len(results)
                pbar.update(new_this_batch)
                pbar.set_postfix(cat=str(cat_name)[:24], n=len(rows), dups=duplicates_skipped)

                _write_rows(rows)
                _save_checkpoint(
                    {
                        "token": token,
                        "n_questions": len(rows),
                        "n_duplicates_skipped": duplicates_skipped,
                        "last_category_index": cat_index,
                        "offset": offset,
                        "status": "in_progress",
                        "expected_verified": expected,
                    }
                )

                if new_this_batch == 0:
                    empty_streak += 1
                    if empty_streak >= 2:
                        break
                else:
                    empty_streak = 0

                _pace()

            _pace()
    finally:
        pbar.close()

    _write_rows(rows)
    _save_checkpoint(
        {
            "token": token,
            "n_questions": len(rows),
            "n_duplicates_skipped": duplicates_skipped,
            "last_category_index": len(categories),
            "offset": offset,
            "status": "complete",
            "expected_verified": expected,
        }
    )
    print(
        f"[ingest] Terminé : {len(rows)} questions uniques "
        f"({duplicates_skipped} doublons écartés) → {config.BRONZE_CSV}"
    )
    return config.BRONZE_CSV


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Scrape intégral OpenTDB vers la couche bronze.")
    parser.add_argument("--force", action="store_true", help="Ignore le checkpoint et re-télécharge.")
    args = parser.parse_args()
    run_ingest(force=args.force)


if __name__ == "__main__":
    main()
