"""Fraud typology detectors.

Everything here is expressed as merges / groupbys / boolean filters so the *same code* runs on
cuDF (GPU) or pandas (CPU). Only small final result sets are moved to host memory.

Edge frame contract: columns tx_id, src, dst, amount, t   (t = epoch seconds, int64)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import backend as B

xdf = B.xdf


# --------------------------------------------------------------------------- feature join
def haversine_km(lat1, lon1, lat2, lon2):
    r = np.pi / 180.0
    p1, p2 = lat1 * r, lat2 * r
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin((lon2 - lon1) * r / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def enrich_edges(tx, accounts):
    """Relational join: attach sender/receiver home country + coordinates, derive cross-border + distance."""
    s = accounts[["account_id", "country", "home_lat", "home_lon"]].rename(columns={
        "account_id": "src", "country": "src_country", "home_lat": "s_lat", "home_lon": "s_lon"})
    d = accounts[["account_id", "country", "home_lat", "home_lon"]].rename(columns={
        "account_id": "dst", "country": "dst_country", "home_lat": "d_lat", "home_lon": "d_lon"})
    out = tx.merge(s, on="src", how="left").merge(d, on="dst", how="left")
    out["cross_border"] = out["src_country"] != out["dst_country"]
    out["km"] = haversine_km(out["s_lat"], out["s_lon"], out["d_lat"], out["d_lon"])
    return out


# --------------------------------------------------------------------------- cycles
def _prune_to_scc(e, min_size: int = 3):
    """Keep only edges whose endpoints share a strongly connected component of >= min_size nodes.
    Any directed cycle lives entirely inside one SCC, so this shrinks the join search space."""
    comp = B.scc_labels(e)
    sizes = comp.groupby("label")["vertex"].count().reset_index().rename(columns={"vertex": "size"})
    comp = comp.merge(sizes, on="label")
    comp = comp[comp["size"] >= min_size]
    ls = comp[["vertex", "label"]].rename(columns={"vertex": "src", "label": "ls"})
    ld = comp[["vertex", "label"]].rename(columns={"vertex": "dst", "label": "ld"})
    e = e.merge(ls, on="src").merge(ld, on="dst")
    return e[e["ls"] == e["ld"]][["tx_id", "src", "dst", "amount", "t"]]


def find_cycles(edges, window_s: int, min_len: int = 3, max_len: int = 5, retention: float = 0.75,
                max_paths: int = 3_000_000, prune: bool = True) -> list[dict]:
    """Enumerate money-laundering cycles with iterative self-joins (path extension + closing join).

    A valid cycle is a directed loop of distinct accounts where each hop happens *after* the previous one,
    the whole loop completes within `window_s`, and every hop forwards between `retention` and 100% of the
    previous hop's amount (launderers pay a small cut at each hop). Strictly increasing timestamps make the
    earliest transfer the unique starting edge, so each loop is found exactly once (no rotation duplicates).
    """
    e = edges[["tx_id", "src", "dst", "amount", "t"]]
    e = e[e["src"] != e["dst"]]
    if prune and len(e):
        e = _prune_to_scc(e)
    if len(e) == 0:
        return []

    paths = e.rename(columns={"src": "n0", "dst": "n1", "amount": "a0", "t": "t0", "tx_id": "x0"})
    found: list[dict] = []

    def hop_ok(df, h):
        return ((df[f"t{h}"] > df[f"t{h-1}"]) & ((df[f"t{h}"] - df["t0"]) <= window_s)
                & (df[f"a{h}"] <= df[f"a{h-1}"]) & (df[f"a{h}"] >= retention * df[f"a{h-1}"]))

    for h in range(1, max_len):
        if len(paths) == 0:
            break
        if h >= min_len - 1:  # closing join: last node -> n0
            close = e.rename(columns={"src": f"n{h}", "dst": "n0", "amount": f"a{h}", "t": f"t{h}", "tx_id": f"x{h}"})
            c = paths.merge(close, on=[f"n{h}", "n0"])
            c = B.to_pandas(c[hop_ok(c, h)])
            for r in c.to_dict("records"):
                found.append({"nodes": [int(r[f"n{j}"]) for j in range(h + 1)],
                              "tx_ids": [int(r[f"x{j}"]) for j in range(h + 1)],
                              "amounts": [float(r[f"a{j}"]) for j in range(h + 1)],
                              "times": [int(r[f"t{j}"]) for j in range(h + 1)]})
        if h == max_len - 1:
            break
        ext = e.rename(columns={"src": f"n{h}", "dst": f"n{h+1}", "amount": f"a{h}", "t": f"t{h}", "tx_id": f"x{h}"})
        p = paths.merge(ext, on=f"n{h}")
        mask = hop_ok(p, h) & (p[f"n{h+1}"] != p["n0"])
        for j in range(1, h + 1):
            mask = mask & (p[f"n{h+1}"] != p[f"n{j}"])
        paths = p[mask]
        if len(paths) > max_paths:  # guard against join explosion on dense graphs
            paths = paths.nlargest(max_paths, "a0")
    return found


def group_rings(cycles: list[dict]) -> list[dict]:
    """Merge repeated laps over the same account set into a single ring record."""
    rings: dict[frozenset, dict] = {}
    for c in cycles:
        r = rings.setdefault(frozenset(c["nodes"]), {"accounts": sorted(c["nodes"]), "tx_ids": set(),
                                                     "principal": 0.0, "start": 10**12, "end": 0, "laps": 0})
        r["tx_ids"].update(c["tx_ids"])
        r["principal"] = max(r["principal"], max(c["amounts"]))
        r["start"], r["end"] = min(r["start"], c["times"][0]), max(r["end"], c["times"][-1])
        r["laps"] += 1
    out = list(rings.values())
    for r in out:
        r["tx_ids"] = sorted(r["tx_ids"])
    return sorted(out, key=lambda r: -r["principal"])


# --------------------------------------------------------------------------- structuring
def find_structuring(edges, band_low: float, band_high: float, min_counterparties: int,
                     min_total: float, window_s: int) -> list[dict]:
    """Many distinct counterparties moving just-under-threshold amounts into (fan-in) or out of
    (fan-out) one account inside a short window."""
    band = edges[(edges["amount"] >= band_low) & (edges["amount"] < band_high)]
    if len(band) == 0:
        return []
    band = band.assign(one=1, tmin=band["t"], tmax=band["t"])
    band_pd = B.to_pandas(band)
    out = []
    for key, other, kind in (("dst", "src", "fan_in"), ("src", "dst", "fan_out")):
        g = band.groupby(key).agg({other: "nunique", "amount": "sum", "one": "sum", "tmin": "min", "tmax": "max"})
        g = g.reset_index().rename(columns={other: "n_counterparties", "amount": "total",
                                            "one": "n_tx", "tmin": "t_first", "tmax": "t_last"})
        g = g[(g["n_counterparties"] >= min_counterparties) & (g["total"] >= min_total)
              & ((g["t_last"] - g["t_first"]) <= window_s)]
        for r in B.to_pandas(g).to_dict("records"):
            rows = band_pd[band_pd[key] == r[key]]
            out.append({"hub": int(r[key]), "kind": kind, "n_counterparties": int(r["n_counterparties"]),
                        "n_tx": int(r["n_tx"]), "total": float(r["total"]),
                        "start": int(r["t_first"]), "end": int(r["t_last"]),
                        "counterparties": sorted(int(x) for x in rows[other].unique()),
                        "tx_ids": sorted(int(x) for x in rows["tx_id"])})
    return sorted(out, key=lambda r: -r["total"])


# --------------------------------------------------------------------------- pass-through mules
def find_pass_through(edges, acct_age, max_age_h: float, min_amount: float, ratio: float,
                      max_delay_s: int) -> list[dict]:
    """Newly created accounts that receive >= min_amount and forward >= `ratio` of it shortly after."""
    e = edges.assign(tmin=edges["t"])
    i = e.groupby("dst").agg({"amount": "sum", "tmin": "min"}).reset_index().rename(
        columns={"dst": "account_id", "amount": "in_total", "tmin": "in_first"})
    o = e.groupby("src").agg({"amount": "sum", "tmin": "min"}).reset_index().rename(
        columns={"src": "account_id", "amount": "out_total", "tmin": "out_first"})
    m = i.merge(o, on="account_id").merge(acct_age[["account_id", "age_h"]], on="account_id")
    m["delay_s"] = m["out_first"] - m["in_first"]
    m = m[(m["age_h"] <= max_age_h) & (m["in_total"] >= min_amount) & (m["out_total"] >= ratio * m["in_total"])
          & (m["delay_s"] >= 0) & (m["delay_s"] <= max_delay_s)]
    return [{"account": int(r["account_id"]), "in_total": float(r["in_total"]), "out_total": float(r["out_total"]),
             "delay_min": float(r["delay_s"]) / 60, "age_h": float(r["age_h"])}
            for r in B.to_pandas(m).to_dict("records")]
