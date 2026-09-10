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


REASONING_MARKER = "__LM_STUDIO_INTERNAL_LSEP_SYNTHETIC_REASONING_END_f4e9a8d2c6b14d0c9e5f3a7b8c1d2e6a__"


def test_clean_answer_keeps_only_what_follows_the_reasoning_marker():
    raw = (
        "The user is asking for the capital of France.\n"
        "The capital of France is Paris.\n"
        "The user requested \"Answer with the answer only.\""
        + REASONING_MARKER
        + "Paris"
    )
    assert clean_answer(raw) == "Paris"


def test_clean_answer_strips_think_blocks():
    assert clean_answer("<think>Let me recall.\nIt is in Europe.</think>\nParis") == "Paris"


def test_clean_answer_is_unchanged_without_reasoning():
    assert clean_answer("Paris") == "Paris"


def test_raw_text_keeps_the_reasoning_for_audit():
    backend = FakeBackend([{"text": "reasoning here" + REASONING_MARKER + "Paris"}])
    result = LLMClient("fake/model", backend=backend).complete("sys", "user")
    assert result.text == "Paris"
    assert REASONING_MARKER in result.raw_text


def test_backend_construction_is_outside_the_timer():
    """Le chargement du modèle ne doit pas être compté dans response_time."""
    import time as _time

    class SlowToBuildBackend:
        def __init__(self):
            _time.sleep(0.2)

        def respond(self, system_prompt, user_prompt):
            return {"text": "Paris"}

    class LazyClient(LLMClient):
        @property
        def backend(self):
            if self._backend is None:
                self._backend = SlowToBuildBackend()
            return self._backend

    result = LazyClient("fake/model").complete("sys", "user")
    assert result.status == "ok"
    assert result.response_time < 0.15, "la construction du backend fuit dans le chronométrage"
