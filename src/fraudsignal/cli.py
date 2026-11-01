"""Command line interface."""

import argparse
import sys
from pathlib import Path
from typing import get_args

from dotenv import find_dotenv, load_dotenv

from fraudsignal.analyzer import (
    AnalysisError,
    ConfigurationError,
    InvalidMessageError,
    analyze,
    create_client,
)
from fraudsignal.schema import Channel

EXIT_ANALYSIS_FAILED = 1
EXIT_BAD_INPUT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fraudsignal",
        description="Analyze a suspicious message. "
        "The result supports a human decision; it is not proof.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser(
        "analyze", help="analyze one message and print a JSON result"
    )
    analyze_parser.add_argument(
        "--channel", required=True, choices=get_args(Channel)
    )
    analyze_parser.add_argument(
        "--file", type=Path, help="UTF-8 text file (default: standard input)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv(find_dotenv(usecwd=True))

    try:
        message = (
            args.file.read_text(encoding="utf-8") if args.file else sys.stdin.read()
        )
        result = analyze(message, args.channel, create_client())
    except (OSError, UnicodeDecodeError, InvalidMessageError, ConfigurationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    except AnalysisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ANALYSIS_FAILED

    print(result.model_dump_json(indent=2))
    return 0
