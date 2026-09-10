### Task 7: Registre des variantes de prompt

**Files:**
- Create: `src/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: rien.
- Produces: `PROMPT_VARIANTS: dict[str, PromptVariant]` avec les clés `p1_constrained_mcq`, `p2_open_minimal`, `p3_open_guided` ; `class PromptVariant` exposant `variant_id: str`, `system_prompt: str`, `build_user_prompt(question_row: Mapping) -> str`, `prompt_hash: str`, `mode: str` (`"constrained"` ou `"open"`) ; `get_variant(variant_id: str) -> PromptVariant`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_prompts.py` :

```python
import pytest

from src.prompts import PROMPT_VARIANTS, get_variant

MULTIPLE = {
    "question": "Who painted the Mona Lisa?",
    "category": "Art",
    "difficulty": "easy",
    "type": "multiple",
    "choices": ["Raphael", "Leonardo da Vinci", "Titian", "Donatello"],
}

BOOLEAN = {
    "question": "The Earth is flat.",
    "category": "Science & Nature",
    "difficulty": "easy",
    "type": "boolean",
    "choices": ["True", "False"],
}


def test_three_variants_are_registered():
    assert set(PROMPT_VARIANTS) == {"p1_constrained_mcq", "p2_open_minimal", "p3_open_guided"}


def test_constrained_variant_lists_the_choices_in_order():
    prompt = get_variant("p1_constrained_mcq").build_user_prompt(MULTIPLE)
    assert prompt.index("Raphael") < prompt.index("Leonardo da Vinci")
    assert "Titian" in prompt


def test_open_variants_never_leak_the_choices():
    for variant_id in ("p2_open_minimal", "p3_open_guided"):
        prompt = get_variant(variant_id).build_user_prompt(MULTIPLE)
        assert "Titian" not in prompt
        assert "Leonardo da Vinci" not in prompt


def test_every_variant_includes_the_question():
    for variant in PROMPT_VARIANTS.values():
        assert "Who painted the Mona Lisa?" in variant.build_user_prompt(MULTIPLE)


def test_boolean_questions_get_true_false_instruction_in_open_modes():
    for variant_id in ("p2_open_minimal", "p3_open_guided"):
        prompt = get_variant(variant_id).build_user_prompt(BOOLEAN)
        assert "True" in prompt and "False" in prompt


def test_modes_are_declared():
    assert get_variant("p1_constrained_mcq").mode == "constrained"
    assert get_variant("p2_open_minimal").mode == "open"
    assert get_variant("p3_open_guided").mode == "open"


def test_prompt_hash_is_stable_and_distinct():
    hashes = {v.variant_id: v.prompt_hash for v in PROMPT_VARIANTS.values()}
    assert len(set(hashes.values())) == 3
    assert get_variant("p1_constrained_mcq").prompt_hash == hashes["p1_constrained_mcq"]


def test_unknown_variant_raises():
    with pytest.raises(KeyError):
        get_variant("nope")
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.prompts'`.

- [ ] **Step 3: Implémenter**

Créer `src/prompts.py` :

```python
"""Registre versionné des variantes de prompt.

L'écart p1 / p3 mesure la différence entre reconnaître une réponse parmi
des options et la restituer librement. L'écart p2 / p3 isole l'apport de
l'ingénierie de prompt à mode d'interrogation constant.

Les prompts restent en anglais, comme le corpus OpenTDB.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Mapping

_CONSTRAINED_SYSTEM = (
    "You are a trivia answering engine. "
    "Reply with the answer only. "
    "No explanation, no extra punctuation, no markdown, no quotes. "
    "Copy one of the given options verbatim."
)

_MINIMAL_SYSTEM = "You are a trivia answering engine. Answer with the answer only."

_GUIDED_SYSTEM = (
    "You are a trivia answering engine. "
    "Reply with the shortest exact answer and nothing else. "
    "No explanation, no reasoning, no full sentence, no leading article, "
    "no trailing period, no markdown, no quotes. "
    "For a person, give the full name. For a date, give the year only unless "
    "the question asks otherwise. For a true/false statement, reply True or False."
)


def _constrained_user(row: Mapping) -> str:
    options = "\n".join(f"- {choice}" for choice in row["choices"])
    return (
        f"Category: {row['category']}\n"
        f"Difficulty: {row['difficulty']}\n"
        f"Question: {row['question']}\n"
        f"Options:\n{options}\n"
        "Copy one option verbatim.\n"
        "Answer:"
    )


def _minimal_user(row: Mapping) -> str:
    suffix = "\nAnswer True or False." if row["type"] == "boolean" else ""
    return f"Question: {row['question']}{suffix}\nAnswer:"


def _guided_user(row: Mapping) -> str:
    if row["type"] == "boolean":
        instruction = "Reply with exactly one word: True or False."
    else:
        instruction = "Reply with the exact answer only, no sentence, no explanation."
    return (
        f"Category: {row['category']}\n"
        f"Difficulty: {row['difficulty']}\n"
        f"Question: {row['question']}\n"
        f"{instruction}\n"
        "Answer:"
    )


@dataclass(frozen=True)
class PromptVariant:
    variant_id: str
    mode: str
    system_prompt: str
    _builder: Callable[[Mapping], str]

    def build_user_prompt(self, question_row: Mapping) -> str:
        return self._builder(question_row)

    @property
    def prompt_hash(self) -> str:
        probe = {
            "question": "<probe>",
            "category": "<category>",
            "difficulty": "<difficulty>",
            "type": "multiple",
            "choices": ["<a>", "<b>"],
        }
        payload = f"{self.system_prompt}␟{self._builder(probe)}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:12]


PROMPT_VARIANTS: dict[str, PromptVariant] = {
    "p1_constrained_mcq": PromptVariant(
        "p1_constrained_mcq", "constrained", _CONSTRAINED_SYSTEM, _constrained_user
    ),
    "p2_open_minimal": PromptVariant(
        "p2_open_minimal", "open", _MINIMAL_SYSTEM, _minimal_user
    ),
    "p3_open_guided": PromptVariant(
        "p3_open_guided", "open", _GUIDED_SYSTEM, _guided_user
    ),
}


def get_variant(variant_id: str) -> PromptVariant:
    if variant_id not in PROMPT_VARIANTS:
        raise KeyError(f"Variante inconnue: {variant_id!r}. Connues: {sorted(PROMPT_VARIANTS)}")
    return PROMPT_VARIANTS[variant_id]
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_prompts.py -v`
Expected: PASS, 8 tests.


---

