import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fraudsignal.analyzer import MAX_MESSAGE_CHARS, ConfigurationError
from fraudsignal import api

MESSAGE = "URGENT : payez ici"
GOOD = json.dumps(
    {
        "verdict": "fraud",
        "confidence": 0.9,
        "signals": [{"type": "urgency", "quote": "URGENT", "explanation": "x"}],
        "summary": "s",
        "recommendation": "r",
    }
)


class FakeClient:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.models = self

    def generate_content(self, **kwargs):
        return SimpleNamespace(text=self.answers.pop(0))


@pytest.fixture
def http():
    def build(*answers):
        api.app.dependency_overrides[api.client_dependency] = lambda: FakeClient(*answers)
        return TestClient(api.app)

    yield build
    api.app.dependency_overrides.clear()


def test_health(http):
    assert http().get("/health").json() == {"status": "ok"}


def test_analyze_returns_validated_result(http):
    response = http(GOOD).post("/analyze", json={"message": MESSAGE, "channel": "sms"})

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "fraud"
    assert body["needs_human_review"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "", "channel": "sms"},
        {"message": "x" * (MAX_MESSAGE_CHARS + 1), "channel": "sms"},
        {"message": MESSAGE, "channel": "telegram"},
        {"message": MESSAGE},
        {"message": MESSAGE, "channel": "sms", "needs_human_review": False},
    ],
)
def test_invalid_requests_are_rejected(http, payload):
    assert http().post("/analyze", json=payload).status_code == 422


def test_blank_message_is_rejected(http):
    response = http().post("/analyze", json={"message": "   ", "channel": "sms"})
    assert response.status_code == 422


def test_failed_analysis_returns_no_verdict_and_no_detail(http):
    response = http("secret upstream text", "secret upstream text").post(
        "/analyze", json={"message": MESSAGE, "channel": "sms"}
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "Analysis unavailable"}


def test_missing_configuration_returns_generic_error(monkeypatch):
    def fail():
        raise ConfigurationError("GEMINI_API_KEY is not set")

    monkeypatch.setattr(api, "get_client", fail)
    response = TestClient(api.app).post(
        "/analyze", json={"message": MESSAGE, "channel": "sms"}
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Service misconfigured"}


def test_measure_summarizes_only_successful_requests():
    from evaluation.measure_api import summarize

    samples = [
        {"status": 200, "latency_s": 1.0},
        {"status": 200, "latency_s": 3.0},
        {"status": 502, "latency_s": 9.0},
    ]
    assert summarize(samples).startswith("2/3 requests succeeded; median 2.00 s")
    assert summarize([{"status": 502, "latency_s": 9.0}]) == "0/1 requests succeeded"


def test_measure_posts_messages_and_sends_token():
    import httpx

    from evaluation.measure_api import measure

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        samples = measure(http, "http://api.test/", 3, 0, "token-value")

    assert len(samples) == 3
    assert str(seen[0].url) == "http://api.test/analyze"
    assert seen[0].headers["authorization"] == "Bearer token-value"
    assert {"message", "channel"} == set(json.loads(seen[0].content))
