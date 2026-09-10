"""Registre versionné des variantes de prompt.

L'écart p1 / p3 mesure la différence entre reconnaître une réponse parmi
des options et la restituer librement. L'écart p2 / p3 isole l'apport de
l'ingénierie de prompt à mode d'interrogation constant.

En p1 les options sont lettrées et le modèle répond par la lettre seule :
une lettre est un jeton unique et non ambigu, là où la recopie verbatim
échouait sur la moindre variation de casse ou de ponctuation. La lettre est
résolue contre `choices` par l'étage `choice_letter` de la cascade.

Les prompts restent en anglais, comme le corpus OpenTDB.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

_CHOICE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_CONSTRAINED_SYSTEM = (
    "You are a trivia answering engine. "
    "Every question comes with lettered options. "
    "Reply with the letter of the correct option and nothing else. "
    "No explanation, no option text, no extra punctuation, no markdown, no quotes."
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


def _lettered_options(choices: Sequence) -> list[str]:
    """Préfixe chaque option d'une lettre, dans l'ordre mélangé reçu.

    Le mélange est seedé par `question_id` en amont, dans l'étage silver.
    L'ordre reçu ici fait foi : la lettre encode la position stockée.
    """
    if not choices:
        raise ValueError("Une question contrainte doit proposer au moins une option.")
    if len(choices) > len(_CHOICE_LETTERS):
        raise ValueError(f"Trop d'options pour un lettrage A-Z : {len(choices)}.")
    return [f"{_CHOICE_LETTERS[i]}. {choice}" for i, choice in enumerate(choices)]


def _constrained_user(row: Mapping) -> str:
    options = "\n".join(_lettered_options(row["choices"]))
    return (
        f"Category: {row['category']}\n"
        f"Difficulty: {row['difficulty']}\n"
        f"Question: {row['question']}\n"
        f"Options:\n{options}\n"
        "Reply with the letter of the correct option.\n"
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
    _builder: Callable[[Mapping], str] = field(repr=False, compare=False)

    def build_user_prompt(self, question_row: Mapping) -> str:
        return self._builder(question_row)

    @property
    def prompt_hash(self) -> str:
        """Empreinte du gabarit, stockée à chaque ligne de résultat.

        Les deux types de question sont rendus : les variantes ouvertes
        produisent une consigne différente pour les booléens, et une dérive
        de ce texte doit changer l'empreinte.
        """
        probe_multiple = {
            "question": "<probe>",
            "category": "<category>",
            "difficulty": "<difficulty>",
            "type": "multiple",
            "choices": ["<a>", "<b>"],
        }
        probe_boolean = {**probe_multiple, "type": "boolean", "choices": ["True", "False"]}
        payload = "␟".join(
            [self.system_prompt, self._builder(probe_multiple), self._builder(probe_boolean)]
        ).encode("utf-8")
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
