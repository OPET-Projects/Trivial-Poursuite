import pandas as pd
import pytest

from src.enrich_llm import (
    load_done_keys,
    partition_dir,
    run_enrich,
    select_pending,
    stratified_sample,
)
from src.llm_client import LLMResult


def read_answers(root):
    """Relit l'arbre des réponses sans inférence de partition.

    `prompt_variant` est à la fois une clé de partition dans le chemin et une
    colonne des fichiers : laisser pyarrow inférer les deux le fait échouer sur
    une fusion de types incompatibles. La colonne du fichier fait foi.
    """
    return pd.read_parquet(root, partitioning=None)


@pytest.fixture
def questions():
    rows = []
    for i in range(6):
        rows.append(
            {
                "question_id": f"q{i:04d}",
                "category": "Art",
                "category_group": "General",
                "category_name": "Art",
                "type": "multiple",
                "difficulty": ["easy", "medium", "hard"][i % 3],
                "question": f"Question {i}?",
                "correct_answer": "A",
                "incorrect_answers": ["B", "C", "D"],
                "choices": ["B", "A", "C", "D"],
                "correct_answer_position": 1,
                "n_choices": 4,
                "correct_answer_norm": "a",
                "answer_is_numeric": False,
                "question_len": 12,
                "answer_len": 1,
                "cleaned_at": "2026-09-10T10:00:00+00:00",
            }
        )
    return pd.DataFrame(rows)


class StubClient:
    def __init__(self, model_name="fake/model", failures=()):
        self.model_name = model_name
        self.calls = []
        self.failures = set(failures)

    def complete(self, system_prompt, user_prompt):
        self.calls.append(user_prompt)
        index = len(self.calls) - 1
        if index in self.failures:
            return LLMResult("", "", 0.1, None, None, "", "error", "boom", 3)
        return LLMResult("A", "A", 0.2, 30, 2, "stop", "ok", "", 1)


@pytest.fixture(autouse=True)
def silver_dirs(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "SILVER_ANSWERS_DIR", tmp_path / "answers")
    monkeypatch.setattr(config, "SILVER_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "SILVER_QUESTIONS", tmp_path / "questions.parquet")
    monkeypatch.setattr(config, "FLUSH_EVERY", 2)
    return tmp_path


def test_partition_path_encodes_model_and_variant():
    path = partition_dir("google_gemma", "p1_constrained_mcq")
    assert path.parts[-2:] == ("model=google_gemma", "prompt_variant=p1_constrained_mcq")


def test_two_models_do_not_share_a_partition():
    assert partition_dir("a", "p1_constrained_mcq") != partition_dir("b", "p1_constrained_mcq")


def test_select_pending_excludes_done_keys(questions):
    pending = select_pending(questions, {"q0000", "q0001"}, None)
    assert set(pending["question_id"]) == {"q0002", "q0003", "q0004", "q0005"}


def test_select_pending_shards_deterministically(questions):
    first = select_pending(questions, set(), (0, 3))
    second = select_pending(questions, set(), (1, 3))
    third = select_pending(questions, set(), (2, 3))
    ids = set(first["question_id"]) | set(second["question_id"]) | set(third["question_id"])
    assert ids == set(questions["question_id"])
    assert not set(first["question_id"]) & set(second["question_id"])


def test_select_pending_shards_are_stable_across_calls(questions):
    """La reprise d'un poste doit retrouver exactement son lot."""
    first = select_pending(questions, set(), (0, 3))
    again = select_pending(questions, set(), (0, 3))
    assert list(first["question_id"]) == list(again["question_id"])


def test_run_enrich_writes_one_partition_per_variant(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq", "p2_open_minimal"], client=StubClient())
    frame = read_answers(silver_dirs / "answers")
    assert len(frame) == 12
    assert set(frame["prompt_variant"]) == {"p1_constrained_mcq", "p2_open_minimal"}


def test_run_enrich_flushes_several_parts(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    parts = list(partition_dir("fake_model", "p1_constrained_mcq").glob("part-*.parquet"))
    assert len(parts) == 3


def test_rerun_is_idempotent(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    second_client = StubClient()
    run_enrich("fake/model", ["p1_constrained_mcq"], client=second_client)
    assert second_client.calls == []
    assert len(read_answers(silver_dirs / "answers")) == 6


def test_a_second_model_does_not_overwrite_the_first(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("model/one", ["p1_constrained_mcq"], client=StubClient("model/one"))
    run_enrich("model/two", ["p1_constrained_mcq"], client=StubClient("model/two"))
    frame = read_answers(silver_dirs / "answers")
    assert len(frame) == 12
    assert set(frame["model_name"]) == {"model/one", "model/two"}


def test_errored_rows_are_retried_on_the_next_run(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient(failures={0, 1}))
    done = load_done_keys("fake_model", "p1_constrained_mcq")
    assert len(done) == 4
    retry_client = StubClient()
    run_enrich("fake/model", ["p1_constrained_mcq"], client=retry_client)
    assert len(retry_client.calls) == 2


def test_errored_rows_are_kept_for_audit(questions, silver_dirs):
    """Une erreur d'infrastructure repasse dans la file mais laisse une trace."""
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient(failures={0, 1}))
    frame = read_answers(silver_dirs / "answers")
    assert (frame["status"] == "error").sum() == 2
    assert set(frame.loc[frame["status"] == "error", "error"]) == {"boom"}


def test_first_row_of_a_run_is_flagged_as_warmup(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    frame = read_answers(silver_dirs / "answers")
    assert frame["is_warmup"].sum() == 1


def test_provenance_columns_are_populated(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    frame = read_answers(silver_dirs / "answers")
    for column in ("host", "hardware", "run_id", "prompt_hash", "model_slug"):
        assert frame[column].notna().all()
        assert (frame[column].astype(str).str.len() > 0).all()


def test_prompt_text_is_stored_for_audit(questions, silver_dirs):
    """Le prompt lettré doit être relisible tel qu'il a été soumis."""
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    frame = read_answers(silver_dirs / "answers")
    assert frame["prompt_text"].str.contains("A. B").all()
    assert frame["prompt_text"].str.contains("B. A").all()


def test_run_metadata_file_is_written(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    assert list((silver_dirs / "runs").glob("run-*.json"))


def test_missing_silver_is_reported_clearly(silver_dirs):
    with pytest.raises(FileNotFoundError):
        run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())


def test_stratified_sample_respects_proportions(questions):
    sampled = stratified_sample(questions, 3, 42)
    assert len(sampled) == 3
    assert sampled["difficulty"].nunique() == 3
