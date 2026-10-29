"""Draw the flagged transaction network (rings / structuring clusters / mules) to a PNG."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd


def draw(findings, tx: pd.DataFrame, path: str, title: str = "Flagged transaction networks"):
    flagged = findings.flagged()
    kind = {}
    for s in findings.structuring:
        for a in s["counterparties"]:
            kind[a] = "structuring feeder"
        kind[s["hub"]] = "structuring hub"
        for a in s["forwarded_to"]:
            kind.setdefault(a, "collector")
    for m in findings.mules:
        kind.setdefault(m["account"], "new-account mule")
    for r in findings.rings:
        for a in r["accounts"]:
            kind[a] = "circular flow"
    sub = tx[(tx["src"].isin(flagged) | tx["dst"].isin(flagged)) & (tx["amount"] >= 5_000)]
    g = nx.from_pandas_edgelist(sub, "src", "dst", ["amount"], create_using=nx.DiGraph)
    for n in g:
        kind.setdefault(n, "unflagged counterparty")
    colors = {"circular flow": "#d62728", "structuring feeder": "#ff9f1c", "structuring hub": "#8c1c13",
              "collector": "#6a4c93", "new-account mule": "#1f77b4", "unflagged counterparty": "#bdbdbd"}
    fig, ax = plt.subplots(figsize=(13, 8))
    pos = nx.spring_layout(g.to_undirected(), seed=3, k=0.55, iterations=200)
    nx.draw_networkx_edges(g, pos, ax=ax, arrows=True, arrowsize=9, edge_color="#999999", alpha=0.7,
                           width=[0.6 + g[u][v]["amount"] / 25_000 for u, v in g.edges()], connectionstyle="arc3,rad=0.08")
    for k, c in colors.items():
        ns = [n for n in g if kind.get(n) == k]
        nx.draw_networkx_nodes(g, pos, nodelist=ns, node_color=c, node_size={"structuring hub": 260, "unflagged counterparty": 40}.get(k, 90),
                               ax=ax, label=f"{k} ({len(ns)})", edgecolors="white", linewidths=0.6)
    ax.legend(loc="lower left", frameon=True)
    ax.set_title(title + f" — {len(g)} accounts, {g.number_of_edges()} transfers ≥ $5k", fontsize=13)
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
