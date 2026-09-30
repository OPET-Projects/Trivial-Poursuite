### Task 10: Primitives de comparaison

**Files:**
- Modify: `src/scoring.py` (réécriture complète)
- Test: `tests/test_scoring.py`

**Interfaces:**
- Consumes: `config`.
- Produces: `normalize(text: object) -> str`, `boolean_label(text: str) -> bool | None`, `resolve_choice_reference(answer: str, choices: list[str]) -> str | None`, `fuzzy_score(a: str, b: str) -> float`, `matches_single_choice(answer_norm: str, choices: list[str]) -> str | None`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_scoring.py` :

```python
import pytest

from src.scoring import (
    boolean_label,
    fuzzy_score,
    matches_single_choice,
    normalize,
    resolve_choice_reference,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Léonard  de Vinci ", "leonard de vinci"),
        ("The Beatles", "beatles"),
        ("A Clockwork Orange", "clockwork orange"),
        ("Rock & Roll!", "rock roll"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_normalize_keeps_digits():
    assert normalize("1789") == "1789"


@pytest.mark.parametrize("raw,expected", [("True", True), ("false", False), ("yes", True),
                                          ("NO", False), ("vrai", True), ("maybe", None)])
def test_boolean_label(raw, expected):
    assert boolean_label(raw) is expected


def test_boolean_label_reads_the_first_token():
    assert boolean_label("True, because gravity") is True


def test_resolve_choice_reference_by_letter():
    choices = ["Paris", "Rome", "Berlin"]
    assert resolve_choice_reference("B", choices) == "Rome"


def test_resolve_choice_reference_by_rank():
    choices = ["Paris", "Rome", "Berlin"]
    assert resolve_choice_reference("option 3", choices) == "Berlin"


def test_resolve_choice_reference_ignores_plain_answers():
    assert resolve_choice_reference("Berlin", ["Paris", "Rome", "Berlin"]) is None


def test_resolve_choice_reference_rejects_out_of_range():
    assert resolve_choice_reference("Z", ["Paris", "Rome"]) is None


def test_matches_single_choice_returns_the_option():
    assert matches_single_choice("rome", ["Paris", "Rome", "Berlin"]) == "Rome"


def test_matches_single_choice_returns_none_when_ambiguous():
    assert matches_single_choice("rome", ["Rome", "rome!"]) is None


def test_fuzzy_score_is_high_for_name_variants():
    assert fuzzy_score("leonardo da vinci", "da vinci") >= 90


def test_fuzzy_score_is_symmetric():
    assert fuzzy_score("abc def", "def abc") == fuzzy_score("def abc", "abc def")
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: FAIL, `ImportError: cannot import name 'boolean_label'`.

- [ ] **Step 3: Réécrire `src/scoring.py`**

```python
"""Primitives de comparaison de réponses.

Ce module ne rend aucun verdict : il fournit les briques que la cascade de
src/judge.py assemble. La règle de sous-chaîne de la version précédente est
supprimée, trop généreuse : « Paris » validait « Paris Hilton ».
"""

from __future__ import annotations

import re
import string
import unicodedata

from rapidfuzz import fuzz

_TRUE = {"true", "yes", "y", "t", "1", "vrai", "oui"}
_FALSE = {"false", "no", "n", "f", "0", "faux", "non"}
_LEADING_ARTICLES = ("the ", "a ", "an ")
_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")
_LETTER_REF = re.compile(r"^(?:option\s*|answer\s*)?([a-z])$", re.IGNORECASE)
_RANK_REF = re.compile(r"^(?:option\s*|answer\s*|number\s*)?(\d{1,2})\.?$", re.IGNORECASE)


def normalize(text: object) -> str:
    if text is None:
        return ""
    value = unicodedata.normalize("NFKD", str(text))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = _PUNCT.sub(" ", value.casefold())
    value = _SPACES.sub(" ", value).strip()
    for article in _LEADING_ARTICLES:
        if value.startswith(article):
            return value[len(article):].strip()
    return value


def boolean_label(text: str) -> bool | None:
    token = normalize(text)
    if not token:
        return None
    if token in _TRUE:
        return True
    if token in _FALSE:
        return False
    first = token.split(" ", 1)[0]
    if first in _TRUE:
        return True
    if first in _FALSE:
        return False
    return None


def resolve_choice_reference(answer: str, choices: list[str]) -> str | None:
    """Résout une réponse qui désigne une option par sa lettre ou son rang."""
    candidate = str(answer).strip()
    if not candidate or len(candidate) > 10:
        return None

    letter = _LETTER_REF.match(candidate)
    if letter:
        index = string.ascii_lowercase.index(letter.group(1).lower())
        return choices[index] if index < len(choices) else None

    rank = _RANK_REF.match(candidate)
    if rank:
        index = int(rank.group(1)) - 1
        return choices[index] if 0 <= index < len(choices) else None

    return None


def matches_single_choice(answer_norm: str, choices: list[str]) -> str | None:
    hits = [choice for choice in choices if normalize(choice) == answer_norm]
    return hits[0] if len(hits) == 1 else None


def fuzzy_score(a: str, b: str) -> float:
    return float(fuzz.token_set_ratio(a, b))
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_scoring.py -v`
Expected: PASS, 19 tests (paramétrages compris).

---

