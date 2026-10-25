import pytest

from evaluation.confusion import one_vs_rest, rates


def test_hand_computed_one_vs_rest():
    # truth A, B, C ; the system says A (right), C (wrong), nothing
    r = one_vs_rest([("A", "A"), ("B", "C"), ("C", None)], n_labels=3)
    assert (r["tp"], r["fp"], r["fn"], r["tn"]) == (1, 1, 2, 5)
    assert r["tp"] + r["fp"] + r["fn"] + r["tn"] == r["items"] * 3
    assert r["precision"] == pytest.approx(1 / 2)
    assert r["recall"] == pytest.approx(1 / 3)
    assert r["f1"] == pytest.approx(2 * 1 / (2 * 1 + 1 + 2))
    assert r["accuracy"] == pytest.approx(6 / 9)
    assert r["item_accuracy"] == pytest.approx(1 / 3)


def test_abstention_is_only_a_false_negative_and_wrong_answer_is_both():
    abstain = one_vs_rest([("A", None)], 4)
    wrong = one_vs_rest([("A", "B")], 4)
    assert (abstain["fp"], abstain["fn"]) == (0, 1) and abstain["precision"] is None
    assert (wrong["fp"], wrong["fn"]) == (1, 1)


def test_empty_input_has_no_rates():
    r = one_vs_rest([], 5)
    assert r["items"] == 0 and r["precision"] is None and r["item_accuracy"] is None
    assert rates(0, 0, 0, 0, 0)["accuracy"] is None
