"""Classification metrics.

Predictions outside the three true classes ("uncertain", "error") stay in the
denominators: they count as misses, so abstaining or failing cannot raise recall.
"""

from dataclasses import dataclass

from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

CLASSES = ["fraud", "spam", "legitimate"]
PREDICTIONS = CLASSES + ["uncertain", "error"]


@dataclass(frozen=True)
class Score:
    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True)
class Metrics:
    total: int
    accuracy: float
    macro_f1: float
    per_class: dict[str, Score]
    fraud_vs_rest: Score
    confusion: list[list[int]]  # rows: CLASSES, columns: PREDICTIONS


def compute_metrics(y_true: list[str], y_pred: list[str]) -> Metrics:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=CLASSES, zero_division=0
    )
    per_class = {
        label: Score(float(p), float(r), float(f), int(s))
        for label, p, r, f, s in zip(CLASSES, precision, recall, f1, support)
    }
    matrix = confusion_matrix(y_true, y_pred, labels=PREDICTIONS)
    correct = sum(t == p for t, p in zip(y_true, y_pred))
    return Metrics(
        total=len(y_true),
        accuracy=correct / len(y_true),
        macro_f1=sum(s.f1 for s in per_class.values()) / len(CLASSES),
        per_class=per_class,
        fraud_vs_rest=per_class["fraud"],
        confusion=[row[: len(PREDICTIONS)] for row in matrix[: len(CLASSES)].tolist()],
    )
