"""TF-IDF and logistic regression baseline, scored out of fold.

Parameters are the library defaults plus word bigrams, with no tuning on this
dataset. Usage: uv run python -m evaluation.baseline
"""

import json
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline

from evaluation.dataset import Message, load_messages

OUTPUT = Path("results/baseline.jsonl")
FOLDS = 5
SEED = 42


def out_of_fold_predictions(messages: list[Message]) -> list[str]:
    """Each message is predicted by a model that never saw it."""
    texts = [m.text for m in messages]
    labels = [m.label for m in messages]
    strata = [f"{m.label}-{m.language}" for m in messages]

    predictions = [""] * len(messages)
    splitter = StratifiedKFold(n_splits=FOLDS, shuffle=True, random_state=SEED)
    for train, test in splitter.split(texts, strata):
        model = make_pipeline(
            TfidfVectorizer(ngram_range=(1, 2)),
            LogisticRegression(max_iter=1000, random_state=SEED),
        )
        model.fit([texts[i] for i in train], [labels[i] for i in train])
        for index, prediction in zip(test, model.predict([texts[i] for i in test])):
            predictions[index] = str(prediction)
    return predictions


def main() -> None:
    messages = load_messages()
    predictions = out_of_fold_predictions(messages)
    OUTPUT.parent.mkdir(exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as output:
        for message, prediction in zip(messages, predictions):
            output.write(json.dumps({"id": message.id, "verdict": prediction}) + "\n")
    print(f"Wrote {len(predictions)} predictions to {OUTPUT}")


if __name__ == "__main__":
    main()
