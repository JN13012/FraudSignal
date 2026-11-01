"""Run the analyzer over a message set, one JSON line per message.

Finished lines are kept, so an interrupted run (daily quota, network) resumes.
Usage: uv run python -m evaluation.run_gemini --output results/gemini.jsonl
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

import httpx
from dotenv import find_dotenv, load_dotenv
from google.genai import errors

from evaluation.dataset import load_messages
from fraudsignal.analyzer import (
    DEFAULT_MODEL,
    AnalysisError,
    analyze,
    create_client,
)

TRANSIENT_CODES = {429, 500, 503}
BACKOFF_SECONDS = (30, 60, 120)


class QuotaExhausted(RuntimeError):
    """Still throttled after every backoff: stop and resume later."""


class Item(Protocol):
    id: str
    channel: str
    text: str


class RecordingClient:
    """Wraps a Gemini client to measure token usage and number of calls."""

    def __init__(self, client):
        self._client = client
        self.models = self
        self.reset()

    def reset(self) -> None:
        self.attempts = 0
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.thought_tokens = 0

    def generate_content(self, **kwargs):
        self.attempts += 1
        response = self._client.models.generate_content(**kwargs)
        usage = getattr(response, "usage_metadata", None)
        if usage:
            self.prompt_tokens += usage.prompt_token_count or 0
            self.output_tokens += usage.candidates_token_count or 0
            self.thought_tokens += usage.thoughts_token_count or 0
        return response


def is_transient(exc: AnalysisError) -> bool:
    cause = exc.__cause__
    if isinstance(cause, errors.APIError):
        return cause.code in TRANSIENT_CODES
    return isinstance(cause, httpx.HTTPError)


def analyze_with_backoff(
    item: Item,
    client: RecordingClient,
    model: str,
    sleep: Callable[[float], None],
):
    for wait in (*BACKOFF_SECONDS, None):
        try:
            return analyze(item.text, item.channel, client, model=model)
        except AnalysisError as exc:
            if not is_transient(exc):
                raise
            if wait is None:
                raise QuotaExhausted(str(exc)) from exc
            print(f"{item.id}: {exc}; waiting {wait}s", file=sys.stderr)
            sleep(wait)


def completed_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {json.loads(line)["id"] for line in lines}


def run(
    items: Iterable[Item],
    client,
    output: Path,
    model: str,
    delay: float = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Analyze what is not yet done and return how many new lines were written."""
    done = completed_ids(output)
    recording = RecordingClient(client)
    output.parent.mkdir(parents=True, exist_ok=True)
    written = 0

    with output.open("a", encoding="utf-8") as file:
        for item in items:
            if item.id in done:
                continue
            if written:
                sleep(delay)

            recording.reset()
            started = time.perf_counter()
            line = {"id": item.id, "model": model}
            try:
                result = analyze_with_backoff(item, recording, model, sleep)
                line |= result.model_dump()
            except AnalysisError as exc:
                # A definitive failure stays in the results and counts as a miss.
                line |= {"verdict": "error", "error": str(exc)}
            line |= {
                "latency_s": round(time.perf_counter() - started, 3),
                "attempts": recording.attempts,
                "prompt_tokens": recording.prompt_tokens,
                "output_tokens": recording.output_tokens,
                "thought_tokens": recording.thought_tokens,
            }
            file.write(json.dumps(line, ensure_ascii=False) + "\n")
            file.flush()
            written += 1
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("results/gemini.jsonl"))
    parser.add_argument("--delay", type=float, default=5, help="seconds between calls")
    args = parser.parse_args()

    load_dotenv(find_dotenv(usecwd=True))
    model = os.getenv("FRAUDSIGNAL_MODEL") or DEFAULT_MODEL
    items = load_messages()
    try:
        written = run(items, create_client(), args.output, model, args.delay)
    except QuotaExhausted as exc:
        print(f"Stopped, resume later: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {written} lines; {len(completed_ids(args.output))}/{len(items)} done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
