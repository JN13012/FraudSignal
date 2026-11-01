import socket

import pytest
from pydantic import ValidationError

from fraudsignal.schema import AnalysisResult, ModelAnalysis


def payload(**overrides):
    data = {
        "verdict": "fraud",
        "confidence": 0.9,
        "signals": [
            {"type": "urgency", "quote": "Act now", "explanation": "Pressure to act."}
        ],
        "summary": "Likely phishing.",
        "recommendation": "Do not click the link.",
    }
    return data | overrides


def test_valid_payload_is_accepted():
    assert ModelAnalysis.model_validate(payload()).verdict == "fraud"


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_out_of_bounds_is_rejected(confidence):
    with pytest.raises(ValidationError):
        ModelAnalysis.model_validate(payload(confidence=confidence))


@pytest.mark.parametrize("confidence", [0, 1])
def test_confidence_bounds_are_accepted(confidence):
    assert ModelAnalysis.model_validate(payload(confidence=confidence))


def test_unknown_verdict_is_rejected():
    with pytest.raises(ValidationError):
        ModelAnalysis.model_validate(payload(verdict="safe"))


def test_unknown_signal_type_is_rejected():
    signal = {"type": "bad_vibes", "quote": "Act now", "explanation": "x"}
    with pytest.raises(ValidationError):
        ModelAnalysis.model_validate(payload(signals=[signal]))


@pytest.mark.parametrize("quote", ["", "   ", "\n"])
def test_blank_quote_is_rejected(quote):
    signal = {"type": "urgency", "quote": quote, "explanation": "x"}
    with pytest.raises(ValidationError):
        ModelAnalysis.model_validate(payload(signals=[signal]))


def test_model_cannot_set_human_review():
    with pytest.raises(ValidationError):
        ModelAnalysis.model_validate(payload(needs_human_review=False))


def test_result_requires_human_review():
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload())


def test_network_is_blocked():
    with pytest.raises(RuntimeError):
        socket.create_connection(("example.com", 443))
