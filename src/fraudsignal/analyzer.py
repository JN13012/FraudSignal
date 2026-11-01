"""Gemini call and the checks applied to its answer."""

import os
import re

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from fraudsignal.prompt import SYSTEM_INSTRUCTION, build_contents
from fraudsignal.schema import AnalysisResult, Channel, ModelAnalysis

DEFAULT_MODEL = "gemini-3.5-flash-lite"
MAX_MESSAGE_CHARS = 20_000
MIN_CONFIDENCE = 0.9  # Chosen on the evaluation set, see docs/evaluation.md.
MIN_QUOTE_CHARS = 3
MAX_ATTEMPTS = 2
TIMEOUT_MS = 60_000

CONTACT_DETAIL = re.compile(
    r"https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.-]+|\+?\d(?:[\s.-]?\d){6,}"
)
SAFE_RECOMMENDATION = (
    "Do not use any link, number or address from this message; verify through a "
    "channel you already trust. / N'utilisez aucun lien, numéro ou adresse de ce "
    "message ; vérifiez par un canal que vous connaissez déjà."
)


class InvalidMessageError(ValueError):
    """The message cannot be analyzed."""


class ConfigurationError(RuntimeError):
    """The local configuration is incomplete."""


class AnalysisError(RuntimeError):
    """No valid analysis could be obtained."""


def create_client() -> genai.Client:
    http_options = types.HttpOptions(timeout=TIMEOUT_MS)
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true"):
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        location = os.getenv("GOOGLE_CLOUD_LOCATION")
        if not project or not location:
            raise ConfigurationError(
                "GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION must be set for Vertex AI"
            )
        return genai.Client(
            vertexai=True, project=project, location=location, http_options=http_options
        )

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ConfigurationError("GEMINI_API_KEY is not set")
    return genai.Client(api_key=api_key, http_options=http_options)


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


def _contact_details(text: str) -> set[str]:
    return {
        re.sub(r"[\s.-]", "", match).lower().rstrip(",;:!?)")
        for match in CONTACT_DETAIL.findall(text)
    }


def _is_valid_quote(quote: str, message: str) -> bool:
    return quote in message and sum(not c.isspace() for c in quote) >= MIN_QUOTE_CHARS


def _apply_checks(analysis: ModelAnalysis, message: str) -> AnalysisResult:
    signals = [s for s in analysis.signals if _is_valid_quote(s.quote, message)]
    citation_rejected = len(signals) < len(analysis.signals)

    # The message's own contact details must not reach the user as advice:
    # an attacker could get the recommendation to send people to their number.
    message_contacts = _contact_details(message)
    hijacked_advice = bool(_contact_details(analysis.recommendation) & message_contacts)
    hijacked_summary = bool(_contact_details(analysis.summary) & message_contacts)

    needs_human_review = (
        analysis.verdict == "uncertain"
        or analysis.confidence < MIN_CONFIDENCE
        or citation_rejected
        or (analysis.verdict in ("fraud", "spam") and not signals)
        or any(s.type == "prompt_injection" for s in signals)
        or hijacked_advice
        or hijacked_summary
    )
    fields = analysis.model_dump(exclude={"signals", "recommendation"})
    return AnalysisResult(
        **fields,
        signals=signals,
        recommendation=SAFE_RECOMMENDATION if hijacked_advice else analysis.recommendation,
        needs_human_review=needs_human_review,
    )
