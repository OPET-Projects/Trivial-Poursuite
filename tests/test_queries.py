"""Tests de la couche d'accès au DuckDB gold.

Les tables de test portent les colonnes réelles des marts de la tâche 14, pas
un schéma jouet : une couche d'accès validée contre trois colonnes ne prouve
rien sur la base que le dashboard ouvrira. Le contenu reste synthétique, mais
la forme est celle que `dbt build` produit.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from app.queries import MARTS, available_marts, connect, load

MARTS_SQL_DIR = Path(__file__).resolve().parents[1] / "dbt_project" / "models" / "marts"


def _gold_tables() -> dict[str, pd.DataFrame]:
    """Une ligne minimale par mart, aux colonnes réelles."""
    return {
        "mart_model_performance": pd.DataFrame(
            [
                {
                    "model_slug": "m1", "model_name": "org/m1", "n_total": 10,
                    "n_scorable": 9, "n_errors": 1, "n_judged": 9, "n_empty": 3,
                    "n_truncated": 3, "accuracy_strict": 0.5,
                    "accuracy_permissive": 0.6, "accuracy_permissive_excl_empty": 0.9,
                    "response_time_median": 14.5, "response_time_p95": 19.6,
                    "completion_tokens_avg": 214.0,
                }
            ]
        ),
        "mart_performance_by_category": pd.DataFrame(
            [
                {
                    "model_slug": "m1", "category": "History",
                    "category_group": "Culture", "category_name": "History",
                    "n_questions": 4, "n_empty": 1, "accuracy_strict": 0.5,
                    "accuracy_permissive": 0.5,
                    "accuracy_permissive_excl_empty": 0.67,
                    "delta_to_model_average": -0.1,
                }
            ]
        ),
        "mart_performance_by_difficulty": pd.DataFrame(
            [
                {
                    "model_slug": "m1", "prompt_variant": "p1_constrained_mcq",
                    "difficulty": "easy", "question_type": "multiple",
                    "n_questions": 3, "n_empty": 1, "accuracy_strict": 0.33,
                    "accuracy_permissive": 0.66,
                    "accuracy_permissive_excl_empty": 1.0,
                    "response_time_median": 12.0,
                }
            ]
        ),
        "mart_prompt_performance": pd.DataFrame(
            [
                {
                    "model_slug": "m1", "prompt_variant": "p1_constrained_mcq",
                    "mode": "constrained", "n_questions": 5, "n_empty": 2,
                    "n_truncated": 2, "accuracy_strict": 0.4,
                    "accuracy_permissive": 0.4,
                    "accuracy_permissive_excl_empty": 0.67,
                    "answer_len_avg": 1.0, "completion_tokens_avg": 230.0,
                    "response_time_median": 14.0,
                }
            ]
        ),
        "mart_latency": pd.DataFrame(
            [
                {
                    "model_slug": "m1", "host": "poste-1", "hardware": "M1 Pro",
                    "prompt_variant": "p1_constrained_mcq", "n_questions": 5,
                    "response_time_avg": 14.4, "response_time_median": 14.5,
                    "response_time_p95": 19.6, "tokens_per_second": 15.2,
                }
            ]
        ),
        "mart_question_hardness": pd.DataFrame(
            [
                {
                    "question_id": "q1", "question": "Who painted the Mona Lisa?",
                    "category": "Art", "difficulty": "easy",
                    "question_type": "multiple", "correct_answer": "Leonardo da Vinci",
                    "n_attempts": 6, "n_judged": 6, "n_correct": 4, "n_empty": 1,
                    "success_rate": 0.66, "failed_by_everyone": False,
                }
            ]
        ),
        "mart_matching_impact": pd.DataFrame(
            [
                {
                    "model_slug": "m1", "prompt_variant": "p3_open_guided",
                    "mode": "open", "match_method": "choice_letter", "n_answers": 1,
                    "share": 0.167, "accuracy_permissive": 1.0,
                    "accuracy_strict": 1.0, "fuzzy_score_avg": 100.0,
                }
            ]
        ),
        "mart_position_bias": pd.DataFrame(
            [
                {
                    "model_slug": "m1", "n_choices": 4, "chosen_position": 1.0,
                    "n_chosen": 3, "n_correct_at_position": 2, "chosen_share": 0.6,
                },
                {
                    "model_slug": "m1", "n_choices": 4, "chosen_position": None,
                    "n_chosen": 2, "n_correct_at_position": 0, "chosen_share": 0.4,
                },
            ]
        ),
        "mart_errors": pd.DataFrame(
            [
                {
                    "model_slug": "m1", "prompt_variant": "p1_constrained_mcq",
                    "host": "poste-1", "run_id": "run-1", "n_errors": 1,
                    "first_error_at": pd.Timestamp("2026-09-10T12:00:00"),
                    "last_error_at": pd.Timestamp("2026-09-10T12:00:00"),
                }
            ]
        ),
    }


@pytest.fixture
def gold(tmp_path):
    """Un DuckDB réel portant les neuf marts aux colonnes de production."""
    path = tmp_path / "benchmark.duckdb"
    conn = duckdb.connect(str(path))
    for name, frame in _gold_tables().items():
        conn.register("frame", frame)
        conn.execute(f"create table {name} as select * from frame")
        conn.unregister("frame")
    conn.close()
    return path


@pytest.fixture
def partial_gold(tmp_path):
    """Une base où dbt n'a construit qu'un mart sur neuf."""
    path = tmp_path / "benchmark.duckdb"
    conn = duckdb.connect(str(path))
    frame = _gold_tables()["mart_model_performance"]
    conn.register("frame", frame)
    conn.execute("create table mart_model_performance as select * from frame")
    conn.close()
    return path


def test_connect_is_read_only(gold):
    conn = connect(gold)
    with pytest.raises(duckdb.Error):
        conn.execute("create table t as select 1")


def test_load_returns_dataframe(gold):
    frame = load(connect(gold), "mart_model_performance")
    assert isinstance(frame, pd.DataFrame)
    assert frame.loc[0, "model_slug"] == "m1"


def test_load_rejects_unknown_table(gold):
    with pytest.raises(ValueError):
        load(connect(gold), "drop table users")


def test_available_marts_lists_only_existing_tables(partial_gold):
    assert available_marts(connect(partial_gold)) == ["mart_model_performance"]


def test_available_marts_lists_the_nine_when_all_are_built(gold):
    assert available_marts(connect(gold)) == list(MARTS)


def test_available_marts_keeps_the_catalogue_order(gold):
    listed = available_marts(connect(gold))
    assert listed == [name for name in MARTS if name in listed]


def test_missing_database_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        connect(tmp_path / "absent.duckdb")


def test_missing_database_message_names_the_command(tmp_path):
    """Le correcteur qui clone le dépôt doit lire quoi lancer, pas une trace."""
    with pytest.raises(FileNotFoundError, match="dbt build"):
        connect(tmp_path / "absent.duckdb")


def test_mart_catalogue_is_complete():
    assert len(MARTS) == 9


def test_catalogue_matches_the_models_on_disk():
    """Garde-fou : un mart ajouté à dbt et oublié dans MARTS serait invisible.

    Le dashboard ne lit que le catalogue. Sans ce test, ajouter un modèle dans
    `dbt_project/models/marts/` le ferait construire par dbt puis ignorer
    silencieusement par l'application — un résultat produit mais jamais montré.
    """
    on_disk = {path.stem for path in MARTS_SQL_DIR.glob("mart_*.sql")}
    assert on_disk == set(MARTS)


def test_load_reads_a_mart_holding_null_positions(gold):
    """`mart_position_bias` porte des positions nulles, elles doivent survivre."""
    frame = load(connect(gold), "mart_position_bias")
    assert frame["chosen_position"].isna().sum() == 1
