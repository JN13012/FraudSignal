import io
import json

import pytest

from fraudsignal import cli
from fraudsignal.analyzer import AnalysisError, ConfigurationError
from fraudsignal.schema import AnalysisResult

RESULT = AnalysisResult(
    verdict="fraud",
    confidence=0.9,
    signals=[{"type": "urgency", "quote": "Payez", "explanation": "Pression à payer."}],
    summary="Hameçonnage probable.",
    recommendation="Ne payez pas.",
    needs_human_review=False,
)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    """Keep tests away from any real .env and API key."""
    monkeypatch.setattr(cli, "load_dotenv", lambda *args: None)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-tests")


@pytest.fixture
def analyzed(monkeypatch):
    """Replace the analyzer and record what it receives."""
    calls = []

    def fake_analyze(message, channel, client):
        calls.append((message, channel))
        return RESULT

    monkeypatch.setattr(cli, "analyze", fake_analyze)
    return calls


def test_file_input_prints_json_on_stdout(tmp_path, analyzed, capsys):
    path = tmp_path / "message.txt"
    path.write_text("Payez maintenant é", encoding="utf-8")

    code = cli.main(["analyze", "--channel", "sms", "--file", str(path)])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert json.loads(captured.out)["verdict"] == "fraud"
    assert "Pression à payer." in captured.out
    assert analyzed == [("Payez maintenant é", "sms")]


def test_stdin_input(monkeypatch, analyzed, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("Bonjour"))

    code = cli.main(["analyze", "--channel", "whatsapp"])

    assert code == 0
    assert analyzed == [("Bonjour", "whatsapp")]
    assert json.loads(capsys.readouterr().out)["needs_human_review"] is False


def test_empty_message_fails_with_message_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("  "))

    code = cli.main(["analyze", "--channel", "sms"])

    captured = capsys.readouterr()
    assert code == cli.EXIT_BAD_INPUT
    assert captured.out == ""
    assert "empty" in captured.err


def test_missing_file_fails(tmp_path, capsys):
    path = tmp_path / "absent.txt"

    code = cli.main(["analyze", "--channel", "email", "--file", str(path)])

    captured = capsys.readouterr()
    assert code == cli.EXIT_BAD_INPUT
    assert captured.out == ""
    assert captured.err.startswith("error:")


def test_non_utf8_file_fails(tmp_path, capsys):
    path = tmp_path / "latin1.txt"
    path.write_bytes("colis bloqué".encode("latin-1"))

    code = cli.main(["analyze", "--channel", "email", "--file", str(path)])

    captured = capsys.readouterr()
    assert code == cli.EXIT_BAD_INPUT
    assert captured.out == ""


def test_missing_api_key_fails(monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setattr("sys.stdin", io.StringIO("Bonjour"))

    code = cli.main(["analyze", "--channel", "sms"])

    captured = capsys.readouterr()
    assert code == cli.EXIT_BAD_INPUT
    assert captured.out == ""
    assert "GEMINI_API_KEY" in captured.err


@pytest.mark.parametrize(
    "error, expected_code",
    [
        (AnalysisError("no valid response"), cli.EXIT_ANALYSIS_FAILED),
        (ConfigurationError("bad config"), cli.EXIT_BAD_INPUT),
    ],
)
def test_analyzer_failure_prints_no_verdict(
    monkeypatch, capsys, error, expected_code
):
    def fail(*args):
        raise error

    monkeypatch.setattr(cli, "analyze", fail)
    monkeypatch.setattr("sys.stdin", io.StringIO("Bonjour"))

    code = cli.main(["analyze", "--channel", "sms"])

    captured = capsys.readouterr()
    assert code == expected_code
    assert captured.out == ""
    assert str(error) in captured.err


def test_unknown_channel_is_refused(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["analyze", "--channel", "telegram"])

    assert exit_info.value.code == 2
