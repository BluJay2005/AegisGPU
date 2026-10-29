"""Compute backend selection.

The detectors are written once against the pandas-compatible DataFrame API.
* With RAPIDS installed (and AML_BACKEND=auto|gpu) `xdf` is **cuDF** and graph
  algorithms run on **cuGraph**: every merge / groupby / filter executes on the GPU.
* Otherwise `xdf` is pandas and graph algorithms use NetworkX, so the project runs
  (and is testable in CI) on any laptop.
"""
from __future__ import annotations

import os

import networkx as nx
import pandas as pd

GPU = False
xdf = pd
cugraph = None

_requested = os.getenv("AML_BACKEND", "auto").lower()
if _requested in ("auto", "gpu"):
    try:
        import cudf as _cudf
        import cugraph as _cugraph

        xdf, cugraph, GPU = _cudf, _cugraph, True
    except Exception as exc:  # ImportError, CUDA runtime errors, ...
        if _requested == "gpu":
            raise RuntimeError("AML_BACKEND=gpu but RAPIDS (cudf/cugraph) could not be loaded") from exc


def name() -> str:
    return "RAPIDS cuDF + cuGraph (GPU)" if GPU else "pandas + NetworkX (CPU fallback)"


def from_pandas(df: pd.DataFrame):
    return xdf.from_pandas(df) if GPU else df


def to_pandas(df) -> pd.DataFrame:
    return df.to_pandas() if GPU else df


def scc_labels(edges) -> pd.DataFrame:
    """Strongly connected components of the directed graph -> DataFrame[vertex, label] (backend frame on GPU)."""
    if GPU:
        g = cugraph.Graph(directed=True)
        g.from_cudf_edgelist(edges[["src", "dst"]], source="src", destination="dst")
        out = cugraph.strongly_connected_components(g)
        return out.rename(columns={"labels": "label"})[["vertex", "label"]]
    g = nx.from_pandas_edgelist(edges, "src", "dst", create_using=nx.DiGraph)
    rows = [(v, i) for i, comp in enumerate(nx.strongly_connected_components(g)) for v in comp]
    return pd.DataFrame(rows, columns=["vertex", "label"])


def pagerank(edges) -> pd.DataFrame:
    """PageRank over the transfer graph -> pandas DataFrame[vertex, pagerank]."""
    if GPU:
        g = cugraph.Graph(directed=True)
        g.from_cudf_edgelist(edges[["src", "dst"]], source="src", destination="dst")
        return cugraph.pagerank(g).to_pandas()[["vertex", "pagerank"]]
    g = nx.from_pandas_edgelist(edges, "src", "dst", create_using=nx.DiGraph)
    pr = nx.pagerank(g, max_iter=50)
    return pd.DataFrame({"vertex": list(pr.keys()), "pagerank": list(pr.values())})
