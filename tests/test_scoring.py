import pytest

from src.prompts import get_variant
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


@pytest.mark.parametrize("raw", ["B", "B.", "B)", "(B)", "Answer: B", "b", "option B",
                                 "The answer is B"])
def test_resolve_choice_reference_accepts_every_decided_form(raw):
    """Les formes actées par l'utilisateur, plus les tournures voisines."""
    assert resolve_choice_reference(raw, ["Paris", "Rome", "Berlin"]) == "Rome"


@pytest.mark.parametrize("raw", ["2.", "(2)", "2)", "number 2"])
def test_resolve_choice_reference_accepts_rank_forms(raw):
    assert resolve_choice_reference(raw, ["Paris", "Rome", "Berlin"]) == "Rome"


def test_resolve_choice_reference_ignores_a_long_sentence():
    answer = "I believe the correct option here is the city of Rome"
    assert resolve_choice_reference(answer, ["Paris", "Rome", "Berlin"]) is None


def test_boolean_lexicon_leaves_the_used_letters_alone():
    """Garde-fou de cascade : l'étage boolean précède l'étage choice_letter.

    Une lettre que `boolean_label` réclame serait interceptée en booléen avant
    d'être résolue en option. Le lexique avale F, N, T et Y, donc le lettrage
    n'est sûr que sur le préfixe A-E. OpenTDB plafonne à 4 propositions, on est
    dans la marge — ce test casse si quelqu'un ajoute une lettre isolée au
    lexique ou étend le lettrage au-delà de E.
    """
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    swallowed = [letter for letter in letters if boolean_label(letter) is not None]
    assert swallowed == ["F", "N", "T", "Y"]
    assert all(boolean_label(letter) is None for letter in "ABCDE")


def test_a_lettered_p1_answer_resolves_against_the_prompt_it_came_from():
    """Boucle complète du lettrage : le prompt porte la lettre, la lettre rend l'option."""
    row = {
        "question": "Who painted the Mona Lisa?",
        "category": "Art",
        "difficulty": "easy",
        "type": "multiple",
        "choices": ["Raphael", "Leonardo da Vinci", "Titian", "Donatello"],
    }
    prompt = get_variant("p1_constrained_mcq").build_user_prompt(row)
    assert "B. Leonardo da Vinci" in prompt
    assert resolve_choice_reference("B", row["choices"]) == "Leonardo da Vinci"


def test_matches_single_choice_returns_the_option():
    assert matches_single_choice("rome", ["Paris", "Rome", "Berlin"]) == "Rome"


def test_matches_single_choice_returns_none_when_ambiguous():
    assert matches_single_choice("rome", ["Rome", "rome!"]) is None


def test_fuzzy_score_is_high_for_name_variants():
    assert fuzzy_score("leonardo da vinci", "da vinci") >= 90


def test_fuzzy_score_is_symmetric():
    assert fuzzy_score("abc def", "def abc") == fuzzy_score("def abc", "abc def")


def test_fuzzy_score_separates_close_numbers():
    """Rappel de la règle amont : le fuzzy est désactivé sur answer_is_numeric.

    Le score reste élevé entre deux années voisines, ce qui est exactement la
    raison pour laquelle la cascade doit court-circuiter cet étage.
    """
    assert fuzzy_score("1789", "1798") >= 50
