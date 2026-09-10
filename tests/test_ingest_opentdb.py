import pandas as pd
import pytest

from src.ingest_opentdb import fetch_category, make_question_id, run_ingest
from src.opentdb_client import ResponseCode


class ScriptedClient:
    """Client OpenTDB scripté : chaque entrée décrit la réponse à un fetch."""

    def __init__(self, script, categories=None, counts=None, duplicate_batches=False):
        self.script = list(script)
        self.duplicate_batches = duplicate_batches
        self.fetch_calls = []
        self.tokens_issued = 0
        self._categories = categories or [{"id": 9, "name": "General Knowledge"}]
        self._counts = counts or {9: 137}

    def request_token(self):
        self.tokens_issued += 1
        return f"token-{self.tokens_issued}"

    def categories(self):
        return self._categories

    def category_count(self, category_id):
        return self._counts.get(category_id, 0)

    def global_verified_count(self):
        return sum(self._counts.values())

    def fetch(self, amount, category_id, token):
        self.fetch_calls.append({"amount": amount, "category_id": category_id, "token": token})
        if not self.script:
            raise AssertionError("fetch non prévu par le test")
        code, count = self.script.pop(0)
        questions = [
            {
                "category": "General Knowledge",
                "type": "multiple",
                "difficulty": "easy",
                "question": f"Q{i}" if self.duplicate_batches else f"Q{len(self.fetch_calls)}-{i}",
                "correct_answer": "A",
                "incorrect_answers": ["B", "C", "D"],
            }
            for i in range(count)
        ]
        return code, questions, {"response_code": code}


def test_question_id_is_stable_across_whitespace_and_case():
    first = make_question_id("Who  painted  it?", "Leonardo da Vinci")
    second = make_question_id("who painted it?", "leonardo DA vinci")
    assert first == second
    assert len(first) == 16


def test_question_id_differs_for_different_answers():
    assert make_question_id("Q", "A") != make_question_id("Q", "B")


def test_ladder_degrades_before_giving_up():
    client = ScriptedClient(
        script=[
            (ResponseCode.SUCCESS, 50),
            (ResponseCode.SUCCESS, 50),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.SUCCESS, 25),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.NO_RESULTS, 0),
        ]
    )
    rows, _ = fetch_category(client, "token-1", 9, set())
    assert len(rows) == 125
    assert [call["amount"] for call in client.fetch_calls] == [50, 50, 50, 25, 25, 10, 5, 1]


def test_category_of_137_returns_137_rows():
    client = ScriptedClient(
        script=[
            (ResponseCode.SUCCESS, 50),
            (ResponseCode.SUCCESS, 50),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.SUCCESS, 25),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.SUCCESS, 10),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.SUCCESS, 1),
            (ResponseCode.SUCCESS, 1),
            (ResponseCode.NO_RESULTS, 0),
            (ResponseCode.NO_RESULTS, 0),
        ]
    )
    rows, _ = fetch_category(client, "token-1", 9, set())
    assert len(rows) == 137


def test_token_empty_stops_the_category_without_reset():
    client = ScriptedClient(script=[(ResponseCode.SUCCESS, 50), (ResponseCode.TOKEN_EMPTY, 0)])
    rows, token = fetch_category(client, "token-1", 9, set())
    assert len(rows) == 50
    assert token == "token-1"
    assert client.tokens_issued == 0


def test_token_not_found_requests_a_new_token_and_continues():
    client = ScriptedClient(
        script=[
            (ResponseCode.TOKEN_NOT_FOUND, 0),
            (ResponseCode.SUCCESS, 10),
            (ResponseCode.TOKEN_EMPTY, 0),
        ]
    )
    rows, token = fetch_category(client, "expired", 9, set())
    assert client.tokens_issued == 1
    assert token == "token-1"
    assert len(rows) == 10


def test_rate_limit_retries_same_amount():
    client = ScriptedClient(
        script=[
            (ResponseCode.RATE_LIMIT, 0),
            (ResponseCode.SUCCESS, 5),
            (ResponseCode.TOKEN_EMPTY, 0),
        ]
    )
    rows, _ = fetch_category(client, "token-1", 9, set())
    assert [call["amount"] for call in client.fetch_calls] == [50, 50, 50]
    assert len(rows) == 5


def test_duplicates_are_skipped_via_seen_set():
    client = ScriptedClient(
        script=[(ResponseCode.SUCCESS, 3), (ResponseCode.SUCCESS, 3), (ResponseCode.TOKEN_EMPTY, 0)],
        duplicate_batches=True,
    )
    seen = set()
    rows, _ = fetch_category(client, "token-1", 9, seen)
    ids = {row["question_id"] for row in rows}
    assert len(rows) == 3, "le second lot est identique au premier, il doit être écarté"
    assert len(ids) == 3
    assert seen == ids


def test_bronze_rows_carry_the_expected_columns():
    client = ScriptedClient(script=[(ResponseCode.SUCCESS, 1), (ResponseCode.TOKEN_EMPTY, 0)])
    rows, _ = fetch_category(client, "token-1", 9, set())
    expected = {
        "question_id",
        "category",
        "type",
        "difficulty",
        "question",
        "correct_answer",
        "incorrect_answers",
        "fetched_at",
        "batch_id",
    }
    assert set(rows[0]) == expected


def test_run_ingest_writes_csv_and_checkpoint(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "BRONZE_CSV", tmp_path / "questions_raw.csv")
    monkeypatch.setattr(config, "BRONZE_RESPONSES_DIR", tmp_path / "_responses")
    monkeypatch.setattr(config, "INGEST_CHECKPOINT", tmp_path / "checkpoint.json")
    client = ScriptedClient(script=[(ResponseCode.SUCCESS, 4), (ResponseCode.TOKEN_EMPTY, 0)])
    run_ingest(client=client)
    frame = pd.read_csv(tmp_path / "questions_raw.csv")
    assert len(frame) == 4
    assert (tmp_path / "checkpoint.json").exists()
    assert any((tmp_path / "_responses").iterdir())
