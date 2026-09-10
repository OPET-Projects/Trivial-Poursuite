import pytest

from src.llm_client import LLMClient, clean_answer


class FakeBackend:
    """Double de test du backend LM Studio."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def respond(self, system_prompt, user_prompt):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_clean_answer_strips_quotes_and_trailing_period():
    assert clean_answer('  "Leonardo da Vinci."  ') == "Leonardo da Vinci"


def test_clean_answer_keeps_internal_punctuation():
    assert clean_answer("Rock & Roll, Part 2") == "Rock & Roll, Part 2"


def test_clean_answer_takes_first_line_only():
    assert clean_answer("Paris\nBecause it is the capital.") == "Paris"


def test_clean_answer_strips_answer_prefix():
    assert clean_answer("Answer: Paris") == "Paris"


def test_complete_returns_text_and_timing():
    backend = FakeBackend([{"text": "Paris", "prompt_tokens": 30, "completion_tokens": 2,
                            "finish_reason": "stop"}])
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.text == "Paris"
    assert result.status == "ok"
    assert result.prompt_tokens == 30
    assert result.completion_tokens == 2
    assert result.response_time >= 0


def test_complete_retries_on_transient_error():
    backend = FakeBackend([RuntimeError("connection reset"), {"text": "Paris"}])
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.status == "ok"
    assert result.attempt == 2
    assert backend.calls == 2


def test_complete_gives_up_after_max_attempts():
    backend = FakeBackend([RuntimeError("boom")] * 3)
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.status == "error"
    assert result.text == ""
    assert "boom" in result.error
    assert backend.calls == 3


def test_missing_token_counts_become_none():
    backend = FakeBackend([{"text": "Paris"}])
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.prompt_tokens is None
    assert result.completion_tokens is None
