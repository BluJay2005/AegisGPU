import pandas as pd

from amlnet import detectors as D
from amlnet.engine import Params, analyze, evaluate


def edges(rows):
    return pd.DataFrame(rows, columns=["tx_id", "src", "dst", "amount", "t"])


def test_finds_time_ordered_cycle_regardless_of_start_node():
    # first transfer leaves the *largest* id: canonicalisation must not depend on id order
    e = edges([(1, 30, 10, 20_000, 100), (2, 10, 20, 19_500, 200), (3, 20, 30, 19_000, 300)])
    rings = D.group_rings(D.find_cycles(e, window_s=3_600))
    assert len(rings) == 1 and rings[0]["accounts"] == [10, 20, 30]


def test_rejects_wrong_time_order_and_amount_growth_and_slow_loops():
    wrong_order = edges([(1, 1, 2, 9_000, 300), (2, 2, 3, 8_900, 200), (3, 3, 1, 8_800, 100)])
    grows = edges([(1, 1, 2, 9_000, 100), (2, 2, 3, 9_500, 200), (3, 3, 1, 9_600, 300)])
    slow = edges([(1, 1, 2, 9_000, 0), (2, 2, 3, 8_900, 10), (3, 3, 1, 8_800, 99_999)])
    for e in (wrong_order, grows, slow):
        assert D.find_cycles(e, window_s=3_600) == []


def test_two_cycle_is_not_a_ring():
    assert D.find_cycles(edges([(1, 1, 2, 5_000, 1), (2, 2, 1, 4_900, 2)]), 3_600) == []


def test_structuring_fan_in():
    rows = [(i, 100 + i, 1, 9_500, 1_000 + i) for i in range(6)] + [(99, 500, 1, 12_000, 1_100)]
    out = D.find_structuring(edges(rows), 9_000, 10_000, 5, 25_000, 3_600)
    assert len(out) == 1 and out[0]["hub"] == 1 and out[0]["n_counterparties"] == 6


def test_structuring_needs_enough_distinct_counterparties():
    rows = [(i, 100, 1, 9_500, 1_000 + i) for i in range(8)]  # same sender 8x
    assert D.find_structuring(edges(rows), 9_000, 10_000, 5, 25_000, 3_600) == []


def test_pass_through_mule_only_if_new_and_fast():
    e = edges([(1, 7, 1, 20_000, 0), (2, 1, 8, 19_000, 600)])
    age = pd.DataFrame({"account_id": [1, 7, 8], "age_h": [2.0, 900.0, 900.0]})
    assert [m["account"] for m in D.find_pass_through(e, age, 24, 10_000, .85, 7_200)] == [1]
    age.loc[0, "age_h"] = 500.0
    assert D.find_pass_through(e, age, 24, 10_000, .85, 7_200) == []


def test_end_to_end_recovers_injected_fraud(data):
    acc, tx = data
    ev = evaluate(analyze(tx, acc), acc)
    assert ev["recall"] >= 0.95 and ev["precision"] >= 0.95, ev


def test_require_new_account_filters_networks(data):
    acc, tx = data
    all_r = analyze(tx, acc, Params()).rings
    new_r = analyze(tx, acc, Params(require_new_account=True)).rings
    assert 0 < len(new_r) <= len(all_r)
    assert all(r["new_accounts"] for r in new_r)


def test_ring_rows_have_audit_trail(data):
    acc, tx = data
    r = analyze(tx, acc).rings[0]
    assert r["tx_ids"] and r["principal"] > 10_000 and r["ring_id"].startswith("RING-")
