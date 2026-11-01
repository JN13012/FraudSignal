"""Gemini call and the checks applied to its answer."""

import os

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from fraudsignal.prompt import SYSTEM_INSTRUCTION, build_contents
from fraudsignal.schema import AnalysisResult, Channel, ModelAnalysis

DEFAULT_MODEL = "gemini-3.5-flash-lite"
MAX_MESSAGE_CHARS = 20_000
MIN_CONFIDENCE = 0.7  # Provisional, to be reassessed against the evaluation set.
MAX_ATTEMPTS = 2
TIMEOUT_MS = 60_000


class InvalidMessageError(ValueError):
    """The message cannot be analyzed."""


class ConfigurationError(RuntimeError):
    """The local configuration is incomplete."""


class AnalysisError(RuntimeError):
    """No valid analysis could be obtained."""


def create_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ConfigurationError("GEMINI_API_KEY is not set")
    return genai.Client(
        api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS)
    )


def analyze(
    message: str,
    channel: Channel,
    client: genai.Client,
    model: str | None = None,
) -> AnalysisResult:
    if not message.strip():
        raise InvalidMessageError("Message is empty")
    if len(message) > MAX_MESSAGE_CHARS:
        # Truncating could hide the fraudulent part of the message.
        raise InvalidMessageError(
            f"Message exceeds {MAX_MESSAGE_CHARS} characters"
        )

    analysis = _request_analysis(message, channel, client, model)
    return _apply_checks(analysis, message)


def _request_analysis(
    message: str, channel: Channel, client: genai.Client, model: str | None
) -> ModelAnalysis:
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        response_json_schema=ModelAnalysis.model_json_schema(),
        temperature=0,
    )
    model = model or os.getenv("FRAUDSIGNAL_MODEL") or DEFAULT_MODEL

    for _ in range(MAX_ATTEMPTS):
        try:
            response = client.models.generate_content(
                model=model,
                contents=build_contents(message, channel),
                config=config,
            )
        except errors.APIError as exc:
            raise AnalysisError(f"Gemini API error {exc.code}: {exc.message}") from exc
        except httpx.HTTPError as exc:
            raise AnalysisError(f"Network error: {type(exc).__name__}") from exc

        try:
            return ModelAnalysis.model_validate_json(response.text or "")
        except ValidationError:
            continue

    raise AnalysisError(f"No valid response after {MAX_ATTEMPTS} attempts")


def _apply_checks(analysis: ModelAnalysis, message: str) -> AnalysisResult:
    signals = [s for s in analysis.signals if s.quote in message]
    citation_rejected = len(signals) < len(analysis.signals)

    needs_human_review = (
        analysis.verdict == "uncertain"
        or analysis.confidence < MIN_CONFIDENCE
        or citation_rejected
        or (analysis.verdict in ("fraud", "spam") and not signals)
    )
    return AnalysisResult(
        **analysis.model_dump(exclude={"signals"}),
        signals=signals,
        needs_human_review=needs_human_review,
    )
