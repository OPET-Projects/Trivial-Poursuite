import json

import pandas as pd

from src.transform_silver import clean_questions, shuffled_choices, split_category


def bronze_frame(**overrides):
    row = {
        "question_id": "abc123def4567890",
        "category": "Entertainment: Film",
        "type": "multiple",
        "difficulty": "easy",
        "question": "Who directed &quot;Jaws&quot;?",
        "correct_answer": "Steven Spielberg",
        "incorrect_answers": json.dumps(["George Lucas", "Ridley Scott", "Brian De Palma"]),
        "fetched_at": "2026-09-10T10:00:00+00:00",
        "batch_id": "cat11-0001",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_split_category_separates_group_and_name():
    assert split_category("Entertainment: Film") == ("Entertainment", "Film")


def test_split_category_without_separator():
    assert split_category("Geography") == ("General", "Geography")


def test_shuffle_is_deterministic_for_a_given_question_id():
    first = shuffled_choices("id-1", "A", ["B", "C", "D"])
    second = shuffled_choices("id-1", "A", ["B", "C", "D"])
    assert first == second


def test_shuffle_differs_between_question_ids():
    orders = {tuple(shuffled_choices(f"id-{i}", "A", ["B", "C", "D"])) for i in range(40)}
    assert len(orders) > 1


def test_shuffle_keeps_every_choice_exactly_once():
    choices = shuffled_choices("id-1", "A", ["B", "C", "D"])
    assert sorted(choices) == ["A", "B", "C", "D"]


def test_boolean_options_are_not_always_false_first():
    positions = {
        shuffled_choices(f"id-{i}", "True", ["False"]).index("False") for i in range(40)
    }
    assert positions == {0, 1}


def test_html_entities_are_decoded():
    cleaned = clean_questions(bronze_frame())
    assert cleaned.loc[0, "question"] == 'Who directed "Jaws"?'


def test_double_encoded_entities_are_decoded():
    cleaned = clean_questions(bronze_frame(question="Rock &amp;amp; Roll"))
    assert cleaned.loc[0, "question"] == "Rock & Roll"


def test_question_id_is_carried_over_untouched():
    cleaned = clean_questions(bronze_frame())
    assert cleaned.loc[0, "question_id"] == "abc123def4567890"


def test_correct_answer_position_matches_choices():
    cleaned = clean_questions(bronze_frame())
    position = cleaned.loc[0, "correct_answer_position"]
    assert cleaned.loc[0, "choices"][position] == "Steven Spielberg"


def test_numeric_answer_is_flagged():
    cleaned = clean_questions(bronze_frame(correct_answer="1789"))
    assert bool(cleaned.loc[0, "answer_is_numeric"]) is True


def test_non_numeric_answer_is_not_flagged():
    cleaned = clean_questions(bronze_frame())
    assert bool(cleaned.loc[0, "answer_is_numeric"]) is False


def test_rows_without_question_are_dropped():
    frame = pd.concat([bronze_frame(), bronze_frame(question="", question_id="zzz")], ignore_index=True)
    assert len(clean_questions(frame)) == 1


def test_duplicate_question_ids_are_dropped():
    frame = pd.concat([bronze_frame(), bronze_frame()], ignore_index=True)
    assert len(clean_questions(frame)) == 1
