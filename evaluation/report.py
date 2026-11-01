"""Build a Markdown report from recorded predictions, so no figure is typed by hand.

Usage: uv run python -m evaluation.report results/baseline.jsonl results/gemini.jsonl
"""

import argparse
import json
import re
import statistics
from pathlib import Path

from evaluation.dataset import Message, load_messages
from evaluation.metrics import CLASSES, PREDICTIONS, compute_metrics

# Standard paid tier, USD per 1M tokens, ai.google.dev/gemini-api/docs/pricing,
# read on 2026-11-01. Output includes thinking tokens.
PRICES = {"gemini-3.5-flash-lite": (0.30, 2.50), "gemini-3.8-flash": (0.75, 3.75)}


def read_predictions(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


STOPWORDS = {
    "fr": {"le", "la", "les", "de", "des", "et", "ce", "vous", "ne", "pas", "un", "une", "est", "votre"},
    "en": {"the", "and", "of", "to", "this", "you", "your", "not", "is", "a", "it", "do"},
    "es": {"el", "los", "las", "y", "no", "su", "para", "este", "haga", "del", "ninguna", "una"},
    "pt": {"o", "os", "as", "e", "não", "seu", "sua", "para", "este", "uma", "do", "nenhum"},
}


def guess_language(text: str) -> str:
    """Rough stopword vote, enough to spot an answer in the wrong language."""
    words = re.findall(r"\w+", text.lower())
    return max(STOPWORDS, key=lambda lang: sum(word in STOPWORDS[lang] for word in words))


def language_mismatches(rows: list[tuple[Message, dict]]) -> list[str]:
    return [
        m.id
        for m, p in rows
        if p.get("recommendation")
        and guess_language(p["summary"] + " " + p["recommendation"]) != m.language
    ]


def overall_section(rows: list[tuple[Message, dict]]) -> list[str]:
    metrics = compute_metrics([m.label for m, _ in rows], [p["verdict"] for _, p in rows])
    lines = [
        f"Accuracy {metrics.accuracy:.3f}, macro F1 {metrics.macro_f1:.3f}, "
        f"on {metrics.total} messages.",
        "",
        "| Class | Precision | Recall | F1 | Support |",
        "|---|---|---|---|---|",
    ]
    for label, score in metrics.per_class.items():
        lines.append(
            f"| {label} | {score.precision:.3f} | {score.recall:.3f} "
            f"| {score.f1:.3f} | {score.support} |"
        )
    lines += [
        "",
        "Confusion matrix (rows: true label, columns: prediction):",
        "",
        "| | " + " | ".join(PREDICTIONS) + " |",
        "|---" * (len(PREDICTIONS) + 1) + "|",
    ]
    for label, row in zip(CLASSES, metrics.confusion):
        lines.append(f"| {label} | " + " | ".join(str(n) for n in row) + " |")
    return lines


def breakdown_section(rows: list[tuple[Message, dict]]) -> list[str]:
    lines = [
        "| Group | Messages | Accuracy | Fraud precision | Fraud recall |",
        "|---|---|---|---|---|",
    ]
    groups = [
        (f"{name} = {value}", key, value)
        for name, key, values in (
            ("language", lambda m: m.language, ("fr", "en")),
            ("channel", lambda m: m.channel, ("email", "sms", "whatsapp")),
            ("hard", lambda m: m.hard, (False, True)),
        )
        for value in values
    ]
    for title, key, value in groups:
        subset = [(m, p) for m, p in rows if key(m) == value]
        if not subset:
            continue
        metrics = compute_metrics(
            [m.label for m, _ in subset], [p["verdict"] for _, p in subset]
        )
        fraud = metrics.fraud_vs_rest
        lines.append(
            f"| {title} | {metrics.total} | {metrics.accuracy:.3f} "
            f"| {fraud.precision:.3f} | {fraud.recall:.3f} |"
        )
    return lines


def review_section(rows: list[tuple[Message, dict]]) -> list[str]:
    reviewed = [(m, p) for m, p in rows if p.get("needs_human_review")]
    automatic = [
        (m, p) for m, p in rows if p["verdict"] != "error" and not p.get("needs_human_review")
    ]
    errors = sum(p["verdict"] == "error" for _, p in rows)

    def accuracy(subset):
        if not subset:
            return "n/a"
        return f"{sum(m.label == p['verdict'] for m, p in subset) / len(subset):.3f}"

    lines = [
        f"- Flagged for human review: {len(reviewed)}/{len(rows)}, accuracy {accuracy(reviewed)}",
        f"- Not flagged: {len(automatic)}/{len(rows)}, accuracy {accuracy(automatic)}",
        f"- Analysis failures: {errors}/{len(rows)}",
        f"- Answers apparently not in the message language: {len(language_mismatches(rows))}/{len(rows)}",
        "",
        "| Confidence | Messages | Accuracy |",
        "|---|---|---|",
    ]
    bands = [(0.0, 0.7), (0.7, 0.9), (0.9, 1.0), (1.0, 1.01)]
    answered = [(m, p) for m, p in rows if p["verdict"] != "error"]
    for low, high in bands:
        band = [(m, p) for m, p in answered if low <= p["confidence"] < high]
        label = "1.0" if low == 1.0 else f"[{low}, {high})"
        lines.append(f"| {label} | {len(band)} | {accuracy(band)} |")
    return lines


def usage_section(predictions: list[dict]) -> list[str]:
    latencies = sorted(p["latency_s"] for p in predictions)
    prompt = sum(p["prompt_tokens"] for p in predictions)
    output = sum(p["output_tokens"] + p["thought_tokens"] for p in predictions)
    calls = sum(p["attempts"] for p in predictions)
    p95 = latencies[min(len(latencies) - 1, round(0.95 * len(latencies)) - 1)]
    lines = [
        f"- Latency per analysis, retries and backoff included: median "
        f"{statistics.median(latencies):.2f} s, p95 {p95:.2f} s, max {latencies[-1]:.2f} s",
        f"- Gemini calls: {calls} for {len(predictions)} messages",
        f"- Tokens: {prompt} input, {output} output including thinking",
    ]
    model = predictions[0].get("model")
    if model in PRICES:
        input_price, output_price = PRICES[model]
        cost = (prompt * input_price + output * output_price) / 1_000_000
        lines.append(
            f"- Estimated paid-tier cost from list prices ({model}): ${cost:.4f} in total, "
            f"${cost / len(predictions):.6f} per message. Not an invoice: "
            "this run used free tier or trial credit."
        )
    return lines


def errors_section(rows: list[tuple[Message, dict]]) -> list[str]:
    wrong = [(m, p) for m, p in rows if m.label != p["verdict"]]
    if not wrong:
        return ["None."]
    lines = ["| Message | True | Predicted | Confidence | Review | Hard |", "|---|---|---|---|---|---|"]
    for message, prediction in wrong:
        lines.append(
            f"| {message.id} | {message.label} | {prediction['verdict']} "
            f"| {prediction.get('confidence', '')} "
            f"| {prediction.get('needs_human_review', '')} | {message.hard} |"
        )
    return lines


def build_report(paths: list[Path], messages: list[Message]) -> str:
    by_id = {m.id: m for m in messages}
    lines = ["# Evaluation results", "", "Generated by `evaluation/report.py`.", ""]
    for path in paths:
        predictions = read_predictions(path)
        rows = [(by_id[p["id"]], p) for p in predictions]
        is_model = "latency_s" in predictions[0]
        lines += [f"## {path.stem}", "", *overall_section(rows), ""]
        if len(rows) < len(messages):
            lines += [f"**Partial run: {len(rows)}/{len(messages)} messages.**", ""]
        lines += ["### Breakdown", "", *breakdown_section(rows), ""]
        if is_model:
            lines += ["### Human review and confidence", "", *review_section(rows), ""]
            lines += ["### Latency and usage", "", *usage_section(predictions), ""]
        lines += ["### Errors", "", *errors_section(rows), ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("results/evaluation.md"))
    args = parser.parse_args()
    args.output.write_text(build_report(args.paths, load_messages()), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
