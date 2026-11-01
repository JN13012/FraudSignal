"""Measure end-to-end latency of a running FraudSignal API.

Usage: uv run python -m evaluation.measure_api --url http://127.0.0.1:8000 --count 20
For a private Cloud Run service, put an identity token in FRAUDSIGNAL_API_TOKEN.
"""

import argparse
import json
import os
import statistics
import time
from pathlib import Path

import httpx

from evaluation.dataset import load_messages

TIMEOUT_SECONDS = 120


def measure(http: httpx.Client, url: str, count: int, delay: float, token: str | None) -> list[dict]:
    messages = load_messages()
    # Spread requests over labels, languages and channels.
    step = max(1, len(messages) // count)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    samples = []
    for message in messages[::step][:count]:
        if samples:
            time.sleep(delay)
        started = time.perf_counter()
        response = http.post(
            f"{url.rstrip('/')}/analyze",
            json={"message": message.text, "channel": message.channel},
            headers=headers,
            timeout=TIMEOUT_SECONDS,
        )
        samples.append(
            {
                "id": message.id,
                "status": response.status_code,
                "latency_s": round(time.perf_counter() - started, 3),
            }
        )
    return samples


def summarize(samples: list[dict]) -> str:
    ok = sorted(s["latency_s"] for s in samples if s["status"] == 200)
    if not ok:
        return f"0/{len(samples)} requests succeeded"
    p95 = ok[min(len(ok) - 1, round(0.95 * len(ok)) - 1)]
    return (
        f"{len(ok)}/{len(samples)} requests succeeded; median "
        f"{statistics.median(ok):.2f} s, p95 {p95:.2f} s, max {ok[-1]:.2f} s"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--delay", type=float, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with httpx.Client() as http:
        samples = measure(
            http, args.url, args.count, args.delay, os.getenv("FRAUDSIGNAL_API_TOKEN")
        )
    if args.output:
        args.output.write_text(
            "".join(json.dumps(sample) + "\n" for sample in samples), encoding="utf-8"
        )
    print(summarize(samples))


if __name__ == "__main__":
    main()
