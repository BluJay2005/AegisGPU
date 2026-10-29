"""Orchestrates detectors over a time window, produces explainable per-account risk scores."""
from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

import pandas as pd

from . import backend as B
from . import detectors as D

EPOCH = pd.Timestamp("1970-01-01")


@dataclass
class Params:
    min_amount: float = 10_000          # ring / mule transfers must be at least this large
    lookback_hours: float = 6           # analysis window ending at the newest transaction
    new_account_hours: float = 24       # "newly created" = younger than this at window end
    require_new_account: bool = False   # only report networks touching >= 1 new account
    cycle_window_hours: float = 6       # a ring must complete within this long
    max_cycle_len: int = 6
    min_cycle_len: int = 3
    retention: float = 0.75             # each hop forwards >= 75% of the previous hop
    band_low: float = 9_000             # structuring band (just under the $10k reporting threshold)
    band_high: float = 10_000
    min_counterparties: int = 5
    min_structuring_total: float = 25_000
    structuring_window_hours: float = 6
    mule_ratio: float = 0.85
    mule_max_delay_hours: float = 2
    flag_threshold: float = 0.5


@dataclass
class Findings:
    params: dict
    backend: str
    anchor: str
    n_tx_window: int
    n_edges_scored: int
    rings: list = field(default_factory=list)
    structuring: list = field(default_factory=list)
    mules: list = field(default_factory=list)
    accounts: list = field(default_factory=list)      # scored accounts, highest risk first
    timings_ms: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    def flagged(self) -> set[int]:
        thr = self.params["flag_threshold"]
        return {a["account"] for a in self.accounts if a["score"] >= thr}


@contextmanager
def _timer(store: dict, key: str):
    t = time.perf_counter()
    yield
    store[key] = round((time.perf_counter() - t) * 1000, 2)


def prepare_tx(tx: pd.DataFrame) -> pd.DataFrame:
    """Do the (cheap) timestamp -> epoch-seconds conversion on host before shipping to the GPU."""
    out = tx.copy()
    out["ts"] = pd.to_datetime(out["ts"])
    out["t"] = ((out["ts"] - EPOCH) // pd.Timedelta(seconds=1)).astype("int64")
    return out


def analyze(tx: pd.DataFrame, accounts: pd.DataFrame, params: Params | None = None,
            anchor: pd.Timestamp | None = None) -> Findings:
    p = params or Params()
    T: dict = {}
    t_all = time.perf_counter()

    tx = prepare_tx(tx)
    anchor = pd.Timestamp(anchor) if anchor is not None else tx["ts"].max()
    tx = tx[tx["ts"] >= anchor - pd.Timedelta(hours=p.lookback_hours)]
    acc = accounts[["account_id", "created_at", "country", "home_lat", "home_lon"]].copy()
    acc["age_h"] = (anchor - pd.to_datetime(acc["created_at"])) / pd.Timedelta(hours=1)
    age_pd = acc.set_index("account_id")["age_h"]

    with _timer(T, "host_to_device"):
        cols = ["tx_id", "src", "dst", "amount", "t"]
        g_tx = B.from_pandas(tx[cols])
        g_acc = B.from_pandas(acc.drop(columns=["created_at"]))

    with _timer(T, "join_enrich_geo"):
        enriched = B.to_pandas(D.enrich_edges(g_tx, g_acc)[["tx_id", "cross_border", "km"]]).set_index("tx_id")

    hi = g_tx[g_tx["amount"] >= p.min_amount]

    with _timer(T, "cycles_scc_prune_plus_joins"):
        rings = D.group_rings(D.find_cycles(hi, int(p.cycle_window_hours * 3600), p.min_cycle_len,
                                            p.max_cycle_len, p.retention))
    with _timer(T, "structuring_groupby"):
        smurf = D.find_structuring(g_tx, p.band_low, p.band_high, p.min_counterparties,
                                   p.min_structuring_total, int(p.structuring_window_hours * 3600))
    with _timer(T, "pass_through_mules"):
        mules = D.find_pass_through(hi, g_acc, p.new_account_hours, p.min_amount, p.mule_ratio,
                                    int(p.mule_max_delay_hours * 3600)) if len(hi) else []
    with _timer(T, "pagerank"):
        pr = B.pagerank(hi) if len(hi) else pd.DataFrame({"vertex": [], "pagerank": []})
    pr_map = dict(zip(pr["vertex"], pr["pagerank"]))

    # ---- decorate with account/geo context (small result sets, host side) ---------------------
    def new_accts(accts):
        return [a for a in accts if age_pd.get(a, 1e9) <= p.new_account_hours]

    for i, r in enumerate(rings, 1):
        sub = enriched.loc[enriched.index.intersection(r["tx_ids"])]
        r.update(ring_id=f"RING-{i:03d}", n_accounts=len(r["accounts"]), n_tx=len(r["tx_ids"]),
                 duration_min=round((r["end"] - r["start"]) / 60, 1), new_accounts=new_accts(r["accounts"]),
                 cross_border_hops=int(sub["cross_border"].sum()), total_km=round(float(sub["km"].sum())),
                 countries=sorted(str(c) for c in set(acc.set_index("account_id").loc[r["accounts"], "country"])))
        r["start"], r["end"] = str(EPOCH + pd.Timedelta(seconds=r["start"])), str(EPOCH + pd.Timedelta(seconds=r["end"]))
        r["principal"] = round(r["principal"], 2)
    for i, s in enumerate(smurf, 1):
        s.update(cluster_id=f"STRUCT-{i:03d}", new_accounts=new_accts(s["counterparties"]),
                 start=str(EPOCH + pd.Timedelta(seconds=s["start"])), end=str(EPOCH + pd.Timedelta(seconds=s["end"])),
                 total=round(s["total"], 2))
        # follow the money: where did the hub forward funds?
        out_edges = tx[(tx["src"] == s["hub"]) & (tx["amount"] >= p.min_amount)]
        s["forwarded_to"] = sorted(int(x) for x in out_edges["dst"].unique()) if s["kind"] == "fan_in" else []
    for m in mules:
        m.update(in_total=round(m["in_total"], 2), out_total=round(m["out_total"], 2),
                 delay_min=round(m["delay_min"], 1), age_h=round(m["age_h"], 1))

    # a ring member that also looks like a pass-through is already explained by its ring
    in_ring = {a for r in rings for a in r["accounts"]}
    mules = [m for m in mules if m["account"] not in in_ring]

    if p.require_new_account:
        rings = [r for r in rings if r["new_accounts"]]
        smurf = [s for s in smurf if s["new_accounts"]]

    # ---- explainable additive risk score -------------------------------------------------------
    score: dict[int, float] = defaultdict(float)
    why: dict[int, list] = defaultdict(list)

    def add(a, w, reason):
        score[int(a)] += w
        why[int(a)].append(reason)

    for r in rings:
        for a in r["accounts"]:
            add(a, 0.55, f"member of circular flow {r['ring_id']}")
            if r["cross_border_hops"]:
                add(a, 0.10, "ring spans multiple countries")
    for s in smurf:
        add(s["hub"], 0.60, f"structuring hub {s['cluster_id']} ({s['n_counterparties']} counterparties)")
        for a in s["counterparties"]:
            add(a, 0.50, f"structuring feeder in {s['cluster_id']}")
        for a in s["forwarded_to"]:
            add(a, 0.50, f"received forwarded structured funds from hub {s['hub']}")
    for m in mules:
        add(m["account"], 0.50, f"new-account pass-through ({m['out_total']/max(m['in_total'],1):.0%} forwarded in {m['delay_min']:.0f} min)")
    for a in list(score):
        if age_pd.get(a, 1e9) <= p.new_account_hours:
            add(a, 0.10, f"account age {age_pd[a]:.1f}h")

    scored = [{"account": a, "score": round(min(s, 1.0), 2), "reasons": why[a], "age_h": round(float(age_pd.get(a, -1)), 1),
               "pagerank": round(float(pr_map.get(a, 0.0)), 5)}
              for a, s in score.items()]
    scored.sort(key=lambda x: (-x["score"], x["account"]))
    T["total"] = round((time.perf_counter() - t_all) * 1000, 2)

    return Findings(params=asdict(p), backend=B.name(), anchor=str(anchor), n_tx_window=int(len(tx)),
                    n_edges_scored=int(len(hi)), rings=rings, structuring=smurf, mules=mules,
                    accounts=scored, timings_ms=T)


def evaluate(f: Findings, accounts: pd.DataFrame) -> dict:
    """Account-level precision / recall against the generator's ground truth."""
    truth = set(accounts.loc[accounts["pattern"] != "", "account_id"].astype(int))
    flagged = f.flagged()
    tp = len(flagged & truth)
    prec = tp / len(flagged) if flagged else 0.0
    rec = tp / len(truth) if truth else 0.0
    per = {}
    for pat, grp in accounts[accounts["pattern"] != ""].groupby("pattern"):
        ids = set(grp["account_id"].astype(int))
        per[pat] = round(len(ids & flagged) / len(ids), 3)
    return {"precision": round(prec, 3), "recall": round(rec, 3),
            "f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
            "flagged": len(flagged), "true_fraud_accounts": len(truth), "true_positives": tp,
            "recall_by_pattern": per}
