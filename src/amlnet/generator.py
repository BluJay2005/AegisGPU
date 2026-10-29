"""Synthetic banking data with *labelled* fraud typologies.

Background traffic: heavy-tailed account popularity, log-normal amounts, uniform timestamps.
Injected typologies (ground truth kept in `accounts.pattern` / `transactions.label`):

* ring        - circular layering across 3-6 (often brand-new, cross-border) accounts, ~3% shaved per hop
* smurf_*     - structuring: 8-15 feeder accounts each send just under $10k to one hub, which forwards to a collector
* mule        - push-payment scam: brand-new account receives a victim's money and forwards ~95% within minutes
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ANCHOR = pd.Timestamp("2026-01-15 12:00:00")  # fixed "now" so datasets are reproducible

COUNTRIES = {
    "US": (39.8, -98.6), "GB": (54.0, -2.0), "DE": (51.2, 10.4), "IN": (21.0, 78.0),
    "BR": (-14.2, -51.9), "MX": (23.6, -102.5), "SG": (1.35, 103.8), "NG": (9.1, 8.7),
    "AE": (24.3, 54.4), "HK": (22.3, 114.2), "CY": (35.1, 33.4), "PA": (8.5, -80.8),
}
_COUNTRY_P = np.array([.46, .11, .09, .08, .05, .05, .04, .03, .03, .02, .02, .02])
OFFSHORE = ["NG", "AE", "HK", "CY", "PA"]
CHANNELS = ["card", "ach", "wire", "p2p"]


def _secs(ts: pd.Timestamp) -> float:
    return float(ts.timestamp())


def generate(n_accounts: int = 20_000, n_tx: int = 200_000, hours: int = 72, seed: int = 7,
             n_rings: int = 8, n_smurf: int = 6, n_mule: int = 10, now: pd.Timestamp = ANCHOR):
    rng = np.random.default_rng(seed)
    now_s = _secs(now)
    ids = np.arange(1_000, 1_000 + n_accounts, dtype=np.int64)

    # ---- accounts -------------------------------------------------------------------------
    age_s = rng.gamma(2.0, 200.0, n_accounts).clip(2, 3650) * 86_400
    new_mask = rng.random(n_accounts) < 0.02  # ~2% legitimately new accounts (noise)
    age_s[new_mask] = rng.uniform(0.5, 48, new_mask.sum()) * 3_600
    countries = list(COUNTRIES)
    cc = rng.choice(countries, n_accounts, p=_COUNTRY_P / _COUNTRY_P.sum())
    base = np.array([COUNTRIES[c] for c in cc])
    accounts = pd.DataFrame({
        "account_id": ids,
        "created_at": pd.to_datetime(now_s - age_s, unit="s"),
        "country": cc,
        "home_lat": (base[:, 0] + rng.normal(0, 3, n_accounts)).round(4),
        "home_lon": (base[:, 1] + rng.normal(0, 3, n_accounts)).round(4),
        "kyc_tier": rng.choice([1, 2, 3], n_accounts, p=[.5, .35, .15]),
        "pattern": "",
    }).set_index("account_id", drop=False)

    # ---- background traffic ---------------------------------------------------------------
    w = rng.pareto(2.5, n_accounts) + 1
    w /= w.sum()
    src = rng.choice(ids, n_tx, p=w)
    dst = rng.choice(ids, n_tx, p=w)
    same = src == dst
    dst[same] = ids[(np.searchsorted(ids, dst[same]) + 1) % n_accounts]
    amount = np.exp(rng.normal(4.1, 1.4, n_tx)).clip(1, 50_000).round(2)
    ts = now_s - rng.uniform(0, hours * 3_600, n_tx)
    rows = [pd.DataFrame({"src": src, "dst": dst, "amount": amount, "ts_s": ts, "label": ""})]

    def emit(s, d, amt, t, label):
        rows.append(pd.DataFrame({"src": [s], "dst": [d], "amount": [round(float(amt), 2)],
                                  "ts_s": [t], "label": [label]}))

    free = list(rng.permutation(ids))

    def take(k):
        out, free[:] = free[:k], free[k:]
        return [int(x) for x in out]

    def mark(members, pattern, new_hours=None, country=None):
        for a in members:
            accounts.loc[a, "pattern"] = pattern
            if new_hours is not None:
                accounts.loc[a, "created_at"] = pd.to_datetime(now_s - rng.uniform(*new_hours) * 3_600, unit="s")
            if country is not None:
                c = country if isinstance(country, str) else rng.choice(country)
                accounts.loc[a, ["country", "home_lat", "home_lon"]] = [c, COUNTRIES[c][0], COUNTRIES[c][1]]

    # ---- typology 1: circular layering rings ----------------------------------------------
    for r in range(n_rings):
        size = int(rng.integers(3, 7))
        m = take(size)
        mark(m, "ring", new_hours=(1, 20) if r % 4 else None, country=OFFSHORE + ["US", "GB"])
        laps = int(rng.integers(1, 3))  # 1-2 laps
        gaps = rng.uniform(2, 15, laps * size) * 60
        amt, t = rng.uniform(15_000, 90_000), now_s - gaps.sum() - rng.uniform(5, 60) * 60  # finish before "now"
        for k in range(laps * size):
            emit(m[k % size], m[(k + 1) % size], amt, t, "ring")
            amt *= 1 - rng.uniform(0.01, 0.05)
            t += gaps[k]

    # ---- typology 2: structuring / smurfing ----------------------------------------------
    for _ in range(n_smurf):
        k = int(rng.integers(8, 16))
        hub, collector, *feeders = take(k + 2)
        mark(feeders, "smurf_feeder", new_hours=(2, 30))
        mark([hub], "smurf_hub")
        mark([collector], "smurf_collector", country=OFFSHORE)
        t0 = now_s - rng.uniform(1.5, 5) * 3_600
        total = 0.0
        for f in feeders:
            a = rng.uniform(9_000, 9_900)
            total += a
            emit(f, hub, a, t0 + rng.uniform(0, 3_600), "smurf")
        emit(hub, collector, total * 0.95, t0 + 4_000, "smurf")

    # ---- typology 3: new-account mules (push-payment scam cash-out) ----------------------
    for _ in range(n_mule):
        victim, mule, benef = take(3)
        mark([mule], "mule", new_hours=(0.5, 5))
        t = now_s - rng.uniform(1.7, 3) * 3_600
        amt = rng.uniform(10_000, 50_000)
        emit(victim, mule, amt, t, "mule")
        emit(mule, benef, amt * rng.uniform(0.9, 0.99), t + rng.uniform(5, 90) * 60, "mule")

    # ---- benign look-alikes: time-ordered triangles but tiny amounts ----------------------
    for _ in range(25):
        a, b, c = take(3)
        t, amt = now_s - rng.uniform(4, 40) * 3_600, rng.uniform(20, 400)
        for s_, d_ in ((a, b), (b, c), (c, a)):
            emit(s_, d_, amt, t, "")
            t += rng.uniform(60, 3_600)
            amt *= 0.98

    tx = pd.concat(rows, ignore_index=True).sort_values("ts_s", kind="stable").reset_index(drop=True)
    tx.insert(0, "tx_id", np.arange(1, len(tx) + 1, dtype=np.int64))
    tx["ts"] = pd.to_datetime(tx.pop("ts_s"), unit="s")
    tx["channel"] = rng.choice(CHANNELS, len(tx))
    home = accounts.loc[tx["src"].to_numpy(), ["home_lat", "home_lon"]].to_numpy()
    tx["geo_lat"] = (home[:, 0] + rng.normal(0, 0.5, len(tx))).round(4)
    tx["geo_lon"] = (home[:, 1] + rng.normal(0, 0.5, len(tx))).round(4)
    tx["amount"] = tx["amount"].astype(float)
    return accounts.reset_index(drop=True), tx[["tx_id", "src", "dst", "amount", "ts", "channel",
                                                 "geo_lat", "geo_lon", "label"]]


_NOTE_TEMPLATES = {
    "ring": [
        "Circular flow across {k} accounts: ${amt:,} cycled back to the origin in {m} minutes with roughly "
        "{fee}% shaved per hop. Accounts opened within {h} hours of activity. Confirmed layering, SAR filed.",
        "Closed-loop transfers between {k} newly opened accounts, funds returned to origin after {m} minutes. "
        "Cross-border to {cc}. Escalated to FIU; accounts frozen.",
    ],
    "structuring": [
        "{n} new accounts each sent ${lo}-${hi} to one beneficiary within {h} hours, deliberately under the $10,000 "
        "reporting threshold. Beneficiary forwarded funds offshore ({cc}). Structuring confirmed.",
        "Fan-in structuring: many small-just-under-threshold deposits consolidated in a single account then wired "
        "out to {cc}. Linked by shared device fingerprint.",
    ],
    "mule": [
        "Newly created account received ${amt:,} from an authorized push-payment scam victim and forwarded ~{pct}% "
        "within {m} minutes. Funds recalled, account closed.",
        "Pass-through mule: account opened {h} hours earlier, inbound wire immediately re-sent to a second "
        "beneficiary. Victim reported romance scam.",
    ],
    "synthetic_id": [
        "Cluster of {n} accounts with near-identical KYC data and a shared device (synthetic identity ring). "
        "Seasoned with low-value activity for {d} days, then bust-out.",
    ],
}


def make_notes(seed: int = 11, n: int = 40, now: pd.Timestamp = ANCHOR) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    kinds = list(_NOTE_TEMPLATES)
    out = []
    for i in range(n):
        kind = kinds[i % len(kinds)]
        tpl = _NOTE_TEMPLATES[kind][int(rng.integers(len(_NOTE_TEMPLATES[kind])))]
        text = tpl.format(
            k=int(rng.integers(3, 7)), amt=int(rng.integers(12, 90)) * 1000, m=int(rng.integers(10, 120)),
            fee=int(rng.integers(1, 6)), h=int(rng.integers(1, 30)), cc=rng.choice(OFFSHORE),
            n=int(rng.integers(6, 16)), lo=9_000, hi=9_900, pct=int(rng.integers(90, 99)), d=int(rng.integers(20, 90)))
        out.append({"note_id": i + 1, "account_id": None, "pattern": kind, "note": text,
                    "created_at": now - pd.Timedelta(days=int(rng.integers(5, 400)))})
    return pd.DataFrame(out)
