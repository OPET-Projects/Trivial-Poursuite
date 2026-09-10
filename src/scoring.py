"""Comparaison de la réponse du modèle avec la bonne réponse."""

from __future__ import annotations

import html
import re
import sys
import unicodedata
from pathlib import Path

from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

_TRUE = {"true", "yes", "y", "t", "1", "vrai", "oui"}
_FALSE = {"false", "no", "n", "f", "0", "faux", "non"}
_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def normalize(text: object) -> str:
    if text is None:
        return ""
    value = html.unescape(str(text)).strip()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold()
    value = _PUNCT.sub(" ", value)
    return _SPACES.sub(" ", value).strip()


def _boolean_label(text: str) -> bool | None:
    token = normalize(text)
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    first = token.split(" ", 1)[0] if token else ""
    if first in _TRUE:
        return True
    if first in _FALSE:
        return False
    return None


def is_ai_correct(
    ai_answer: object,
    correct_answer: object,
    question_type: str,
    all_answers: list[str] | None = None,
) -> bool:
    predicted = normalize(ai_answer)
    expected = normalize(correct_answer)
    if not predicted or not expected:
        return False

    if question_type == "boolean":
        pred_bool = _boolean_label(predicted)
        exp_bool = _boolean_label(expected)
        if pred_bool is not None and exp_bool is not None:
            return pred_bool is exp_bool

    if predicted == expected:
        return True
    if expected in predicted or predicted in expected:
        # évite les sous-chaînes trop courtes ("a", "the")
        if min(len(predicted), len(expected)) >= 4:
            return True

    if all_answers:
        matches = [ans for ans in all_answers if normalize(ans) == predicted]
        if len(matches) == 1:
            return normalize(matches[0]) == expected

    ratio = fuzz.ratio(predicted, expected)
    return ratio >= config.FUZZY_RATIO_THRESHOLD
