import json
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors

from fraudsignal.analyzer import (
    DEFAULT_MODEL,
    MAX_MESSAGE_CHARS,
    AnalysisError,
    ConfigurationError,
    InvalidMessageError,
    analyze,
    create_client,
)
from fraudsignal.prompt import SYSTEM_INSTRUCTION

MESSAGE = "URGENT : votre colis est bloqué. Payez 2 € sur http://colis.example"


class FakeClient:
    """Replays scripted answers: a string is returned, an exception is raised."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(text=answer)


def answer(**overrides):
    data = {
        "verdict": "fraud",
        "confidence": 0.9,
        "signals": [
            {"type": "urgency", "quote": "URGENT", "explanation": "Pression."}
        ],
        "summary": "Hameçonnage probable.",
        "recommendation": "Ne cliquez pas sur le lien.",
    }
    return json.dumps(data | overrides)


def signal(quote):
    return {"type": "other", "quote": quote, "explanation": "x"}


def test_clean_fraud_analysis_needs_no_review():
    result = analyze(MESSAGE, "sms", FakeClient(answer()))

    assert result.verdict == "fraud"
    assert result.signals[0].quote == "URGENT"
    assert result.needs_human_review is False


def test_request_separates_instructions_from_message():
    client = FakeClient(answer())
    analyze(MESSAGE, "whatsapp", client)

    call = client.calls[0]
    assert call["config"].system_instruction == SYSTEM_INSTRUCTION
    assert call["config"].response_mime_type == "application/json"
    assert MESSAGE not in SYSTEM_INSTRUCTION
    assert f"<message>\n{MESSAGE}\n</message>" in call["contents"]
    assert "Channel: whatsapp" in call["contents"]


@pytest.mark.parametrize(
    "quote",
    [
        "Virement immédiat exigé",  # invented
        "urgent",  # case differs
        "votre  colis",  # spacing differs
        "colis bloqué",  # paraphrased
    ],
)
def test_inexact_citation_is_removed_and_triggers_review(quote):
    signals = [signal("URGENT"), signal(quote)]
    result = analyze(MESSAGE, "sms", FakeClient(answer(signals=signals)))

    assert [s.quote for s in result.signals] == ["URGENT"]
    assert result.needs_human_review is True


def test_accented_citation_is_accepted():
    signals = [signal("votre colis est bloqué")]
    result = analyze(MESSAGE, "sms", FakeClient(answer(signals=signals)))

    assert len(result.signals) == 1
    assert result.needs_human_review is False


def test_uncertain_verdict_triggers_review():
    result = analyze(MESSAGE, "sms", FakeClient(answer(verdict="uncertain")))
    assert result.needs_human_review is True


def test_confidence_below_threshold_triggers_review():
    result = analyze(MESSAGE, "sms", FakeClient(answer(confidence=0.69)))
    assert result.needs_human_review is True


def test_confidence_at_threshold_needs_no_review():
    result = analyze(MESSAGE, "sms", FakeClient(answer(confidence=0.7)))
    assert result.needs_human_review is False


@pytest.mark.parametrize("verdict", ["fraud", "spam"])
def test_verdict_without_signal_triggers_review(verdict):
    client = FakeClient(answer(verdict=verdict, signals=[]))
    assert analyze(MESSAGE, "sms", client).needs_human_review is True


def test_all_citations_rejected_triggers_review():
    client = FakeClient(answer(signals=[signal("invented")]))
    result = analyze(MESSAGE, "sms", client)

    assert result.signals == []
    assert result.needs_human_review is True


def test_legitimate_without_signal_needs_no_review():
    client = FakeClient(answer(verdict="legitimate", signals=[]))
    assert analyze(MESSAGE, "sms", client).needs_human_review is False


@pytest.mark.parametrize(
    "invalid",
    ["not json", "", None, answer(confidence=1.5), answer(needs_human_review=False)],
)
def test_invalid_response_is_retried_once(invalid):
    client = FakeClient(invalid, answer())
    result = analyze(MESSAGE, "sms", client)

    assert result.verdict == "fraud"
    assert len(client.calls) == 2


def test_two_invalid_responses_raise_instead_of_returning_a_verdict():
    client = FakeClient("not json", "still not json", answer())

    with pytest.raises(AnalysisError):
        analyze(MESSAGE, "sms", client)
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "failure",
    [
        errors.ClientError(403, {"error": {"message": "denied", "status": "DENIED"}}),
        errors.ServerError(503, {"error": {"message": "busy", "status": "BUSY"}}),
        httpx.ConnectTimeout("timed out"),
    ],
)
def test_api_failure_raises_analysis_error_without_retry(failure):
    client = FakeClient(failure, answer())

    with pytest.raises(AnalysisError):
        analyze(MESSAGE, "sms", client)
    assert len(client.calls) == 1


@pytest.mark.parametrize("message", ["", "   \n\t"])
def test_empty_message_is_rejected_before_any_call(message):
    client = FakeClient()

    with pytest.raises(InvalidMessageError):
        analyze(message, "sms", client)
    assert client.calls == []


def test_too_long_message_is_rejected_before_any_call():
    client = FakeClient()

    with pytest.raises(InvalidMessageError):
        analyze("x" * (MAX_MESSAGE_CHARS + 1), "sms", client)
    assert client.calls == []


def test_message_at_size_limit_is_accepted():
    client = FakeClient(answer(signals=[signal("x")]))
    assert analyze("x" * MAX_MESSAGE_CHARS, "sms", client)


def test_default_model_is_used(monkeypatch):
    monkeypatch.delenv("FRAUDSIGNAL_MODEL", raising=False)
    client = FakeClient(answer())
    analyze(MESSAGE, "sms", client)
    assert client.calls[0]["model"] == DEFAULT_MODEL


def test_empty_model_variable_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("FRAUDSIGNAL_MODEL", "")
    client = FakeClient(answer())
    analyze(MESSAGE, "sms", client)
    assert client.calls[0]["model"] == DEFAULT_MODEL


def test_model_variable_overrides_default(monkeypatch):
    monkeypatch.setenv("FRAUDSIGNAL_MODEL", "some-other-model")
    client = FakeClient(answer())
    analyze(MESSAGE, "sms", client)
    assert client.calls[0]["model"] == "some-other-model"


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        create_client()


def test_client_creation_needs_no_network(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-tests")
    assert create_client()
