import importlib

import config


def test_paths_are_under_data_dir():
    assert config.BRONZE_CSV.name == "questions_raw.csv"
    assert config.SILVER_QUESTIONS.suffix == ".parquet"
    assert config.SILVER_ANSWERS_DIR.name == "answers"
    assert config.SILVER_JUDGMENTS_DIR.name == "judgments"
    assert config.GOLD_DUCKDB.suffix == ".duckdb"


def test_model_name_comes_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "some/other-model")
    reloaded = importlib.reload(config)
    assert reloaded.MODEL_NAME == "some/other-model"
    monkeypatch.delenv("LLM_MODEL")
    importlib.reload(config)


def test_amount_ladder_is_descending_and_ends_at_one():
    assert config.AMOUNT_LADDER == [50, 25, 10, 5, 1]


def test_rate_limit_has_margin_over_api_minimum():
    assert config.RATE_LIMIT_SECONDS >= 5.1
