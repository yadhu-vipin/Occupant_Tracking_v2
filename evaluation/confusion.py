"""TP / FP / FN / TN and the usual rates, in one place.

Every confusion matrix in this project is *one-vs-rest over labels*. An item (an event, or an occupant in one
state) has one true label and at most one predicted label out of ``n_labels`` possible ones:

    TP  predicted label == true label
    FP  a label was predicted, but it is not the true one      (counted once per wrong prediction)
    FN  the true label was not predicted                       (wrong prediction, or no prediction at all)
    TN  every other (item, label) pair  =  items * n_labels - TP - FP - FN

So a wrong prediction is both a FP and a FN, and an abstention is only a FN. Precision, recall and F1 ignore the
(huge) number of TNs; ``accuracy`` includes them and is therefore close to 1 for many labels -- the label-level
"top-1" rate ``item_accuracy`` (TP / items) is the one to quote for identification.
"""
from __future__ import annotations

from typing import Hashable, Iterable, Optional, Tuple


def _safe(num, den):
    return num / den if den else None


def rates(tp: int, fp: int, fn: int, tn: int, items: int) -> dict:
    total = tp + fp + fn + tn
    return {"items": items, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": _safe(tp, tp + fp), "recall": _safe(tp, tp + fn),
            "f1": _safe(2 * tp, 2 * tp + fp + fn), "accuracy": _safe(tp + tn, total),
            "item_accuracy": _safe(tp, items)}


def one_vs_rest(pairs: Iterable[Tuple[Hashable, Optional[Hashable]]], n_labels: int) -> dict:
    """``pairs`` = (true label, predicted label or None) per item."""
    tp = fp = fn = items = 0
    for true, predicted in pairs:
        items += 1
        if predicted is not None and predicted == true:
            tp += 1
        else:
            fn += 1
            if predicted is not None:
                fp += 1
    return rates(tp, fp, fn, items * n_labels - tp - fp - fn, items)
