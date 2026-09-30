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
