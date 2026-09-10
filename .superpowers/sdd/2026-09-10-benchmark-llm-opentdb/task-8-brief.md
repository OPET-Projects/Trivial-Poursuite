### Task 8: Wrapper LM Studio

**Files:**
- Create: `src/llm_client.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: `config`, SDK `lmstudio`.
- Produces: `@dataclass LLMResult` avec `text: str`, `raw_text: str`, `response_time: float`, `prompt_tokens: int | None`, `completion_tokens: int | None`, `finish_reason: str`, `status: str`, `error: str`, `attempt: int` ; `class LLMClient(model_name: str, backend=None)` avec `complete(system_prompt: str, user_prompt: str) -> LLMResult` ; `clean_answer(text: str) -> str`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_llm_client.py` :

```python
import pytest

from src.llm_client import LLMClient, clean_answer


class FakeBackend:
    """Double de test du backend LM Studio."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def respond(self, system_prompt, user_prompt):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_clean_answer_strips_quotes_and_trailing_period():
    assert clean_answer('  "Leonardo da Vinci."  ') == "Leonardo da Vinci"


def test_clean_answer_keeps_internal_punctuation():
    assert clean_answer("Rock & Roll, Part 2") == "Rock & Roll, Part 2"


def test_clean_answer_takes_first_line_only():
    assert clean_answer("Paris\nBecause it is the capital.") == "Paris"


def test_clean_answer_strips_answer_prefix():
    assert clean_answer("Answer: Paris") == "Paris"


def test_complete_returns_text_and_timing():
    backend = FakeBackend([{"text": "Paris", "prompt_tokens": 30, "completion_tokens": 2,
                            "finish_reason": "stop"}])
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.text == "Paris"
    assert result.status == "ok"
    assert result.prompt_tokens == 30
    assert result.completion_tokens == 2
    assert result.response_time >= 0


def test_complete_retries_on_transient_error():
    backend = FakeBackend([RuntimeError("connection reset"), {"text": "Paris"}])
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.status == "ok"
    assert result.attempt == 2
    assert backend.calls == 2


def test_complete_gives_up_after_max_attempts():
    backend = FakeBackend([RuntimeError("boom")] * 3)
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.status == "error"
    assert result.text == ""
    assert "boom" in result.error
    assert backend.calls == 3


def test_missing_token_counts_become_none():
    backend = FakeBackend([{"text": "Paris"}])
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.llm_client'`.

- [ ] **Step 3: Implémenter**

Créer `src/llm_client.py` :

```python
"""Appel au runtime local, chronométré et instrumenté.

Le backend est injectable : les tests n'appellent jamais LM Studio, et
remplacer le runtime ne touche que LMStudioBackend.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

_PREFIX = re.compile(r"^(answer|réponse)\s*[:\-]\s*", re.IGNORECASE)
_WRAPPING_QUOTES = re.compile(r'^["\'`«»\s]+|["\'`«»\s]+$')


def clean_answer(text: str) -> str:
    """Ramène une sortie de modèle à sa réponse nue."""
    if not text:
        return ""
    first_line = text.strip().split("\n")[0]
    first_line = _PREFIX.sub("", first_line.strip())
    first_line = _WRAPPING_QUOTES.sub("", first_line)
    if first_line.endswith(".") and not first_line.endswith(".."):
        first_line = first_line[:-1]
    return first_line.strip()


@dataclass
class LLMResult:
    text: str
    raw_text: str
    response_time: float
    prompt_tokens: int | None
    completion_tokens: int | None
    finish_reason: str
    status: str
    error: str
    attempt: int


class LMStudioBackend:
    """Adaptateur du SDK lmstudio.

    Les noms de champs de statistiques varient selon la version du SDK : on
    les lit défensivement plutôt que de supposer un schéma.
    """

    def __init__(self, model_name: str) -> None:
        import lmstudio as lms

        lms.set_sync_api_timeout(config.LMSTUDIO_TIMEOUT_SECONDS)
        self._lms = lms
        self._model = lms.llm(model_name)

    @staticmethod
    def _stat(stats: Any, *names: str) -> int | None:
        for name in names:
            value = getattr(stats, name, None)
            if isinstance(value, (int, float)):
                return int(value)
        return None

    def respond(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        chat = self._lms.Chat(system_prompt)
        chat.add_user_message(user_prompt)
        result = self._model.respond(
            chat,
            config={"temperature": config.LLM_TEMPERATURE, "maxTokens": config.LLM_MAX_TOKENS},
        )
        stats = getattr(result, "stats", None)
        return {
            "text": str(getattr(result, "content", result) or ""),
            "prompt_tokens": self._stat(stats, "prompt_tokens_count", "promptTokensCount"),
            "completion_tokens": self._stat(stats, "predicted_tokens_count", "predictedTokensCount"),
            "finish_reason": str(getattr(stats, "stop_reason", "") or ""),
        }


class LLMClient:
    def __init__(self, model_name: str = config.MODEL_NAME, backend: Any = None) -> None:
        self.model_name = model_name
        self._backend = backend

    @property
    def backend(self) -> Any:
        if self._backend is None:
            try:
                self._backend = LMStudioBackend(self.model_name)
            except Exception as exc:  # noqa: BLE001 — message actionnable pour l'utilisateur
                raise RuntimeError(
                    "Impossible de joindre LM Studio. Vérifiez que l'application est ouverte, "
                    "que le serveur est démarré (onglet Developer) et que le modèle "
                    f"{self.model_name} est chargé."
                ) from exc
        return self._backend

    def complete(self, system_prompt: str, user_prompt: str) -> LLMResult:
        last_error = ""
        for attempt in range(1, config.LLM_MAX_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                payload = self.backend.respond(system_prompt, user_prompt)
                elapsed = time.perf_counter() - started
                raw = str(payload.get("text") or "")
                return LLMResult(
                    text=clean_answer(raw),
                    raw_text=raw,
                    response_time=elapsed,
                    prompt_tokens=payload.get("prompt_tokens"),
                    completion_tokens=payload.get("completion_tokens"),
                    finish_reason=str(payload.get("finish_reason") or ""),
                    status="ok",
                    error="",
                    attempt=attempt,
                )
            except Exception as exc:  # noqa: BLE001 — une panne ne doit pas arrêter le run
                elapsed = time.perf_counter() - started
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < config.LLM_MAX_ATTEMPTS:
                    time.sleep(min(2 ** attempt, 10))

        return LLMResult(
            text="",
            raw_text="",
            response_time=elapsed,
            prompt_tokens=None,
            completion_tokens=None,
            finish_reason="",
            status="error",
            error=last_error,
            attempt=config.LLM_MAX_ATTEMPTS,
        )
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_llm_client.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Vérifier les statistiques réellement exposées par le SDK**

LM Studio doit tourner avec un modèle chargé.

Run: `python -c "
from src.llm_client import LLMClient
r = LLMClient().complete('Answer with the answer only.', 'Question: Capital of France?\nAnswer:')
print(r)
"`
Expected: `status='ok'`, texte non vide. Si `prompt_tokens` et `completion_tokens` sont `None`, inspecter `dir(result.stats)` dans `LMStudioBackend.respond` et compléter la liste de noms passée à `_stat`. Le correctif 9 n'est acquis que si ces compteurs sont peuplés.

---

