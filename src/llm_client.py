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
_REASONING_END = re.compile(r"__LM_STUDIO_INTERNAL_LSEP_[A-Z_]*REASONING_END_[0-9a-f]+__")
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Isole la réponse finale d'un modèle à raisonnement.

    LM Studio insère un marqueur déterministe entre la réflexion et la
    réponse; d'autres modèles encadrent leur réflexion de balises <think>.
    Aucun prompt système ne supprime ce comportement de façon fiable, donc
    il est traité ici plutôt qu'espéré.
    """
    without_blocks = _THINK_BLOCK.sub("", text)
    parts = _REASONING_END.split(without_blocks)
    return parts[-1] if parts else without_blocks


def clean_answer(text: str) -> str:
    """Ramène une sortie de modèle à sa réponse nue."""
    if not text:
        return ""
    text = strip_reasoning(text)
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
            backend = self.backend  # résolution hors chronomètre : la construction
                                     # paresseuse charge le modèle en mémoire
            started = time.perf_counter()
            try:
                payload = backend.respond(system_prompt, user_prompt)
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
