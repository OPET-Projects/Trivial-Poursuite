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


def test_prompt_hash_covers_the_boolean_branch(monkeypatch):
    """Une dérive de la consigne booléenne doit changer l'empreinte."""
    variant = get_variant("p3_open_guided")
    before = variant.prompt_hash
    original = variant._builder

    def drifted(row):
        text = original(row)
        return text.replace("True or False", "True / False") if row["type"] == "boolean" else text

    object.__setattr__(variant, "_builder", drifted)
    try:
        assert variant.prompt_hash != before
    finally:
        object.__setattr__(variant, "_builder", original)


def test_prompt_hash_is_unchanged_by_repeated_access():
    variant = get_variant("p1_constrained_mcq")
    assert variant.prompt_hash == variant.prompt_hash
