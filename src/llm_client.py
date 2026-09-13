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
from typing import Any, Mapping

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


# Vocabulaire du SDK lmstudio, conservé parce que les runs déjà écrits le
# portent et que dbt détecte la troncature sur `maxPredictedTokensReached`.
_FINISH_REASONS = {"stop": "eosFound", "length": "maxPredictedTokensReached"}


def _token_count(usage: Mapping[str, Any], name: str) -> int | None:
    value = usage.get(name)
    return int(value) if isinstance(value, (int, float)) else None


def parse_completion(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Traduit une réponse chat/completions vers le contrat du backend.

    Seul `content` est retenu : la réflexion éventuelle arrive à part, dans
    `reasoning_content`, et ne doit jamais être notée comme réponse.
    """
    choices = payload.get("choices") or []
    if not choices:
        raise ValueError(f"Réponse sans choix: {payload!r:.200}")
    choice = choices[0]
    usage = payload.get("usage") or {}
    finish = str(choice.get("finish_reason") or "")
    return {
        "text": str((choice.get("message") or {}).get("content") or ""),
        "prompt_tokens": _token_count(usage, "prompt_tokens"),
        "completion_tokens": _token_count(usage, "completion_tokens"),
        "finish_reason": _FINISH_REASONS.get(finish, finish),
    }


class LMStudioBackend:
    """Adaptateur de l'API compatible OpenAI du serveur LM Studio.

    Le SDK lmstudio ne sait pas couper le raisonnement des modèles qui en ont
    un (gemma-4-26b-a4b, bonsai-27b) ; l'API REST le fait via `reasoning_effort`. Sans
    cette coupure, le protocole diffère des modèles déjà mesurés et chaque
    appel coûte dix fois plus de jetons.
    """

    def __init__(self, model_name: str) -> None:
        import requests

        self._session = requests.Session()
        self._model_name = model_name
        self._url = f"{config.LMSTUDIO_BASE_URL}/v1/chat/completions"
        models = self._session.get(
            f"{config.LMSTUDIO_BASE_URL}/v1/models", timeout=config.HTTP_TIMEOUT_SECONDS
        )
        models.raise_for_status()
        known = {entry.get("id") for entry in models.json().get("data", [])}
        if model_name not in known:
            raise LookupError(f"Modèle absent de LM Studio: {model_name}. Connus: {sorted(known)}")

    def respond(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        response = self._session.post(
            self._url,
            json={
                "model": self._model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": config.LLM_TEMPERATURE,
                "max_tokens": config.LLM_MAX_TOKENS,
                "reasoning_effort": config.LLM_REASONING_EFFORT,
                "stream": False,
            },
            timeout=config.LMSTUDIO_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return parse_completion(response.json())


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
