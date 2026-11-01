import json
import re
from collections import Counter
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from google.genai import errors

from evaluation.baseline import out_of_fold_predictions
from evaluation.dataset import load_messages
from evaluation.metrics import compute_metrics
from evaluation.report import build_report
from evaluation.run_gemini import QuotaExhausted, completed_ids, run


@dataclass(frozen=True)
class Item:
    id: str
    text: str = "Payez ici"
    channel: str = "sms"


GOOD = json.dumps(
    {
        "verdict": "fraud",
        "confidence": 0.9,
        "signals": [{"type": "urgency", "quote": "Payez", "explanation": "x"}],
        "summary": "s",
        "recommendation": "r",
    }
)
USAGE = SimpleNamespace(
    prompt_token_count=100, candidates_token_count=20, thoughts_token_count=5
)


class FakeClient:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0
        self.models = self

    def generate_content(self, **kwargs):
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(text=answer, usage_metadata=USAGE)


def throttled():
    return errors.ClientError(429, {"error": {"message": "quota", "status": "LIMIT"}})


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_dataset_is_balanced_across_every_cell():
    cells = Counter((m.label, m.language, m.channel) for m in load_messages())
    assert len(cells) == 18
    assert set(cells.values()) == {10}


def test_dataset_ids_are_unique():
    messages = load_messages()
    assert len({m.id for m in messages}) == len(messages)


def test_dataset_links_use_only_the_reserved_example_domain():
    url = re.compile(r"https?://([^/\s]+)")
    hosts = {h for m in load_messages() for h in url.findall(m.text)}
    assert hosts
    assert all(host.rstrip(".").endswith(".example") for host in hosts)


def test_baseline_predicts_every_message():
    messages = load_messages()
    predictions = out_of_fold_predictions(messages)
    assert len(predictions) == len(messages)
    assert set(predictions) <= {"fraud", "spam", "legitimate"}


def test_metrics_on_known_values():
    metrics = compute_metrics(
        ["fraud", "fraud", "spam", "legitimate"],
        ["fraud", "spam", "spam", "fraud"],
    )
    assert metrics.accuracy == pytest.approx(0.5)
    assert metrics.fraud_vs_rest.precision == pytest.approx(0.5)
    assert metrics.fraud_vs_rest.recall == pytest.approx(0.5)
    assert metrics.per_class["spam"].recall == pytest.approx(1)
    assert metrics.confusion[0] == [1, 1, 0, 0, 0]


def test_uncertain_and_error_count_as_misses():
    metrics = compute_metrics(
        ["fraud", "fraud", "fraud", "spam"],
        ["fraud", "uncertain", "error", "spam"],
    )
    assert metrics.fraud_vs_rest.recall == pytest.approx(1 / 3)
    assert metrics.fraud_vs_rest.precision == pytest.approx(1)
    assert metrics.accuracy == pytest.approx(0.5)
    assert metrics.confusion[0] == [1, 0, 0, 1, 1]


def test_run_records_result_and_usage(tmp_path):
    output = tmp_path / "out.jsonl"
    written = run([Item("a")], FakeClient(GOOD), output, "model-x", sleep=lambda s: None)

    line = read_lines(output)[0]
    assert written == 1
    assert line["id"] == "a"
    assert line["verdict"] == "fraud"
    assert line["model"] == "model-x"
    assert line["attempts"] == 1
    assert line["prompt_tokens"] == 100
    assert line["output_tokens"] == 20
    assert line["thought_tokens"] == 5
    assert line["latency_s"] >= 0


def test_run_resumes_without_repeating_finished_messages(tmp_path):
    output = tmp_path / "out.jsonl"
    run([Item("a")], FakeClient(GOOD), output, "m", sleep=lambda s: None)
    client = FakeClient(GOOD)

    written = run([Item("a"), Item("b")], client, output, "m", sleep=lambda s: None)

    assert written == 1
    assert client.calls == 1
    assert completed_ids(output) == {"a", "b"}


def test_invalid_answers_are_recorded_as_errors(tmp_path):
    output = tmp_path / "out.jsonl"
    client = FakeClient("not json", "not json")
    run([Item("a")], client, output, "m", sleep=lambda s: None)

    line = read_lines(output)[0]
    assert line["verdict"] == "error"
    assert line["attempts"] == 2


def test_throttling_waits_then_succeeds(tmp_path):
    output = tmp_path / "out.jsonl"
    waits = []
    client = FakeClient(throttled(), GOOD)

    run([Item("a")], client, output, "m", sleep=waits.append)

    assert waits == [30]
    assert read_lines(output)[0]["verdict"] == "fraud"


def test_persistent_throttling_stops_and_keeps_earlier_results(tmp_path):
    output = tmp_path / "out.jsonl"
    client = FakeClient(GOOD, *[throttled() for _ in range(4)])

    with pytest.raises(QuotaExhausted):
        run([Item("a"), Item("b")], client, output, "m", sleep=lambda s: None)

    assert completed_ids(output) == {"a"}


def test_report_covers_partial_run_and_usage(tmp_path):
    messages = load_messages()
    path = tmp_path / "gemini.jsonl"
    lines = [
        {
            "id": messages[0].id,
            "verdict": "fraud",
            "confidence": 0.9,
            "needs_human_review": False,
            "model": "gemini-3.5-flash-lite",
            "latency_s": 1.0,
            "attempts": 1,
            "prompt_tokens": 1000,
            "output_tokens": 100,
            "thought_tokens": 0,
        },
        {
            "id": messages[-1].id,
            "verdict": "error",
            "error": "boom",
            "model": "gemini-3.5-flash-lite",
            "latency_s": 2.0,
            "attempts": 2,
            "prompt_tokens": 0,
            "output_tokens": 0,
            "thought_tokens": 0,
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines))

    report = build_report([path], messages)

    assert "Partial run: 2/180" in report
    assert "Analysis failures: 1/2" in report
    assert "$0.0006 in total" in report
    assert messages[-1].id in report
