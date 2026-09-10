### Task 4: Client HTTP OpenTDB

Extraction du transport hors de `src/ingest_opentdb.py`, avec le rythme respecté par les retries et le décodage base64.

**Files:**
- Create: `src/opentdb_client.py`
- Test: `tests/test_opentdb_client.py`

**Interfaces:**
- Consumes: `config`, `src.io_utils`.
- Produces:
  - `class ResponseCode` avec `SUCCESS = 0`, `NO_RESULTS = 1`, `INVALID_PARAMETER = 2`, `TOKEN_NOT_FOUND = 3`, `TOKEN_EMPTY = 4`, `RATE_LIMIT = 5`
  - `class RateLimiter(min_interval: float)` avec `wait() -> None`
  - `class OpenTDBClient(session=None, limiter=None)` avec `request_token() -> str`, `reset_token(token: str) -> None`, `categories() -> list[dict]`, `category_count(category_id: int) -> int`, `global_verified_count() -> int`, `fetch(amount: int, category_id: int, token: str) -> tuple[int, list[dict], dict]` renvoyant `(response_code, questions_décodées, payload_brut)`
  - `decode_field(value: str) -> str`

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_opentdb_client.py` :

```python
import base64

import pytest

from src.opentdb_client import OpenTDBClient, RateLimiter, ResponseCode, decode_field


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Rejoue une liste de réponses et enregistre les paramètres reçus."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if not self.responses:
            raise AssertionError("Appel HTTP non prévu par le test")
        return self.responses.pop(0)


class FakeLimiter(RateLimiter):
    def __init__(self):
        super().__init__(min_interval=0.0)
        self.waits = 0

    def wait(self):
        self.waits += 1


def b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def question_payload(text="Who painted it?"):
    return {
        "category": b64("Art"),
        "type": b64("multiple"),
        "difficulty": b64("easy"),
        "question": b64(text),
        "correct_answer": b64("Leonardo da Vinci"),
        "incorrect_answers": [b64("Raphael"), b64("Titian"), b64("Donatello")],
    }


def test_decode_field_handles_base64():
    assert decode_field(b64("Don't forget π")) == "Don't forget π"


def test_fetch_decodes_every_field():
    session = FakeSession([FakeResponse({"response_code": 0, "results": [question_payload()]})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    code, questions, _ = client.fetch(amount=50, category_id=25, token="tok")
    assert code == ResponseCode.SUCCESS
    assert questions[0]["question"] == "Who painted it?"
    assert questions[0]["incorrect_answers"] == ["Raphael", "Titian", "Donatello"]


def test_fetch_sends_expected_parameters():
    session = FakeSession([FakeResponse({"response_code": 0, "results": []})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    client.fetch(amount=25, category_id=9, token="tok")
    _, params = session.calls[0]
    assert params == {"amount": 25, "category": 9, "token": "tok", "encode": "base64"}


def test_fetch_never_sends_offset_or_api_key():
    session = FakeSession([FakeResponse({"response_code": 0, "results": []})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    client.fetch(amount=50, category_id=9, token="tok")
    _, params = session.calls[0]
    assert "offset" not in params
    assert "apiKey" not in params


def test_limiter_is_consulted_before_every_call():
    limiter = FakeLimiter()
    session = FakeSession(
        [
            FakeResponse({"response_code": 0, "results": []}),
            FakeResponse({"response_code": 0, "results": []}),
        ]
    )
    client = OpenTDBClient(session=session, limiter=limiter)
    client.fetch(amount=50, category_id=9, token="tok")
    client.fetch(amount=50, category_id=10, token="tok")
    assert limiter.waits == 2


def test_retry_also_waits_between_attempts():
    limiter = FakeLimiter()
    session = FakeSession(
        [
            FakeResponse({}, status_code=500),
            FakeResponse({"response_code": 0, "results": []}),
        ]
    )
    client = OpenTDBClient(session=session, limiter=limiter)
    code, _, _ = client.fetch(amount=50, category_id=9, token="tok")
    assert code == ResponseCode.SUCCESS
    assert limiter.waits == 2


def test_request_token_raises_when_absent():
    session = FakeSession([FakeResponse({"response_code": 0})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    with pytest.raises(RuntimeError):
        client.request_token()


def test_category_count_reads_verified_total():
    payload = {
        "category_id": 25,
        "category_question_count": {
            "total_question_count": 137,
            "total_easy_question_count": 40,
        },
    }
    session = FakeSession([FakeResponse(payload)])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    assert client.category_count(25) == 137


def test_rate_limiter_enforces_interval(monkeypatch):
    now = {"t": 100.0}
    slept = []
    monkeypatch.setattr("src.opentdb_client.time.monotonic", lambda: now["t"])
    monkeypatch.setattr("src.opentdb_client.time.sleep", lambda s: slept.append(s))
    limiter = RateLimiter(min_interval=5.1)
    limiter.wait()
    limiter.wait()
    assert slept and pytest.approx(slept[-1], abs=0.01) == 5.1
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_opentdb_client.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.opentdb_client'`.

- [ ] **Step 3: Implémenter**

Créer `src/opentdb_client.py` :

```python
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

    def reset_token(self, token: str) -> None:
        self._get("api_token.php", {"command": "reset", "token": token})

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
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_opentdb_client.py -v`
Expected: PASS, 9 tests.


---

