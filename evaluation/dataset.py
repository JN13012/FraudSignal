"""Labeled evaluation messages. All of them are fictional."""

import json
from dataclasses import dataclass
from pathlib import Path

DATA_PATH = Path(__file__).parent.parent / "data" / "messages.json"


@dataclass(frozen=True)
class Message:
    id: str
    label: str
    language: str
    channel: str
    hard: bool
    text: str


def load_messages(path: Path = DATA_PATH) -> list[Message]:
    tree = json.loads(path.read_text(encoding="utf-8"))
    messages = []
    for label, languages in tree.items():
        for language, channels in languages.items():
            for channel, items in channels.items():
                for index, item in enumerate(items, start=1):
                    is_plain = isinstance(item, str)
                    messages.append(
                        Message(
                            id=f"{label}-{language}-{channel}-{index:02d}",
                            label=label,
                            language=language,
                            channel=channel,
                            hard=False if is_plain else item["hard"],
                            text=item if is_plain else item["text"],
                        )
                    )
    return messages
