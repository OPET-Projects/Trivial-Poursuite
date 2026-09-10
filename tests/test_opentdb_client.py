import base64

import pytest

from src.opentdb_client import OpenTDBClient, RateLimiter, ResponseCode, decode_field


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Rejoue une liste de réponses et enregistre les paramètres reçus."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if not self.responses:
            raise AssertionError("Appel HTTP non prévu par le test")
        return self.responses.pop(0)


class FakeLimiter(RateLimiter):
    def __init__(self):
        super().__init__(min_interval=0.0)
        self.waits = 0

    def wait(self):
        self.waits += 1


def b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def question_payload(text="Who painted it?"):
    return {
        "category": b64("Art"),
        "type": b64("multiple"),
        "difficulty": b64("easy"),
        "question": b64(text),
        "correct_answer": b64("Leonardo da Vinci"),
        "incorrect_answers": [b64("Raphael"), b64("Titian"), b64("Donatello")],
    }


def test_decode_field_handles_base64():
    assert decode_field(b64("Don't forget π")) == "Don't forget π"


def test_fetch_decodes_every_field():
    session = FakeSession([FakeResponse({"response_code": 0, "results": [question_payload()]})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    code, questions, _ = client.fetch(amount=50, category_id=25, token="tok")
    assert code == ResponseCode.SUCCESS
    assert questions[0]["question"] == "Who painted it?"
    assert questions[0]["incorrect_answers"] == ["Raphael", "Titian", "Donatello"]


def test_fetch_sends_expected_parameters():
    session = FakeSession([FakeResponse({"response_code": 0, "results": []})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    client.fetch(amount=25, category_id=9, token="tok")
    _, params = session.calls[0]
    assert params == {"amount": 25, "category": 9, "token": "tok", "encode": "base64"}


def test_fetch_never_sends_offset_or_api_key():
    session = FakeSession([FakeResponse({"response_code": 0, "results": []})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    client.fetch(amount=50, category_id=9, token="tok")
    _, params = session.calls[0]
    assert "offset" not in params
    assert "apiKey" not in params


def test_limiter_is_consulted_before_every_call():
    limiter = FakeLimiter()
    session = FakeSession(
        [
            FakeResponse({"response_code": 0, "results": []}),
            FakeResponse({"response_code": 0, "results": []}),
        ]
    )
    client = OpenTDBClient(session=session, limiter=limiter)
    client.fetch(amount=50, category_id=9, token="tok")
    client.fetch(amount=50, category_id=10, token="tok")
    assert limiter.waits == 2


def test_retry_also_waits_between_attempts():
    limiter = FakeLimiter()
    session = FakeSession(
        [
            FakeResponse({}, status_code=500),
            FakeResponse({"response_code": 0, "results": []}),
        ]
    )
    client = OpenTDBClient(session=session, limiter=limiter)
    code, _, _ = client.fetch(amount=50, category_id=9, token="tok")
    assert code == ResponseCode.SUCCESS
    assert limiter.waits == 2


def test_request_token_raises_when_absent():
    session = FakeSession([FakeResponse({"response_code": 0})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    with pytest.raises(RuntimeError):
        client.request_token()


def test_category_count_reads_verified_total():
    payload = {
        "category_id": 25,
        "category_question_count": {
            "total_question_count": 137,
            "total_easy_question_count": 40,
        },
    }
    session = FakeSession([FakeResponse(payload)])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    assert client.category_count(25) == 137


def test_rate_limiter_enforces_interval(monkeypatch):
    now = {"t": 100.0}
    slept = []
    monkeypatch.setattr("src.opentdb_client.time.monotonic", lambda: now["t"])
    monkeypatch.setattr("src.opentdb_client.time.sleep", lambda s: slept.append(s))
    limiter = RateLimiter(min_interval=5.1)
    limiter.wait()
    limiter.wait()
    assert slept and pytest.approx(slept[-1], abs=0.01) == 5.1


def test_categories_returns_the_list():
    payload = {"trivia_categories": [{"id": 9, "name": "General Knowledge"}]}
    session = FakeSession([FakeResponse(payload)])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    assert client.categories() == [{"id": 9, "name": "General Knowledge"}]


def test_categories_raises_when_empty():
    session = FakeSession([FakeResponse({"trivia_categories": []})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    with pytest.raises(RuntimeError):
        client.categories()


def test_global_verified_count_reads_the_overall_block():
    payload = {"overall": {"total_num_of_questions": 21617, "total_num_of_verified_questions": 5298}}
    session = FakeSession([FakeResponse(payload)])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    assert client.global_verified_count() == 5298


def test_global_verified_count_defaults_to_zero_when_absent():
    session = FakeSession([FakeResponse({})])
    client = OpenTDBClient(session=session, limiter=FakeLimiter())
    assert client.global_verified_count() == 0
