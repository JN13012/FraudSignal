"""Output models. The result is decision support, not proof of fraud."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Channel = Literal["email", "sms", "whatsapp"]
Verdict = Literal["fraud", "spam", "legitimate", "uncertain"]
SignalType = Literal[
    "credential_request",
    "payment_request",
    "suspicious_link",
    "impersonation",
    "urgency",
    "threat",
    "too_good_to_be_true",
    "unsolicited_marketing",
    "prompt_injection",
    "other",
]

NonBlank = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


class Signal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SignalType
    quote: NonBlank
    explanation: NonBlank


class ModelAnalysis(BaseModel):
    """What the model is asked to produce. Human review is decided by our code."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    confidence: float = Field(ge=0, le=1)
    signals: list[Signal]
    summary: NonBlank
    recommendation: NonBlank


class AnalysisResult(ModelAnalysis):
    """Final output: citations checked, human review decided."""

    needs_human_review: bool
