"""HTTP API. The result supports a human decision; it is not proof."""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from google import genai
from pydantic import BaseModel, ConfigDict, Field

from fraudsignal.analyzer import (
    MAX_MESSAGE_CHARS,
    AnalysisError,
    ConfigurationError,
    InvalidMessageError,
    analyze,
    create_client,
)
from fraudsignal.schema import AnalysisResult, Channel

logger = logging.getLogger(__name__)

app = FastAPI(title="FraudSignal", version="0.1.0")


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    channel: Channel


@lru_cache
def get_client() -> genai.Client:
    return create_client()


def client_dependency() -> genai.Client:
    try:
        return get_client()
    except ConfigurationError as exc:
        logger.error("Configuration error: %s", exc)
        raise HTTPException(status_code=500, detail="Service misconfigured") from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze")
def analyze_message(
    request: AnalyzeRequest,
    client: Annotated[genai.Client, Depends(client_dependency)],
) -> AnalysisResult:
    # Messages are never logged: they can hold personal data.
    try:
        return analyze(request.message, request.channel, client)
    except InvalidMessageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AnalysisError as exc:
        logger.error("Analysis failed: %s", exc)
        raise HTTPException(status_code=502, detail="Analysis unavailable") from exc
