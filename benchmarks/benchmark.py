"""Stage-level benchmark. Run the same command on a CPU box and on a GPU box (AML_BACKEND=gpu) and compare.

Stress configuration: whole 72h of traffic, $100 floor, so the join/graph stages operate on hundreds of
thousands of edges instead of the handful that a "$10k in 6 hours" question produces. This measures
THROUGHPUT only; thresholds are deliberately loose, so detection quality is not meaningful here.
"""
from __future__ import annotations

import json
import platform
import time
from pathlib import Path

from amlnet import backend as B
from amlnet.engine import Params, analyze
from amlnet.generator import generate

STRESS = dict(min_amount=100, lookback_hours=72, cycle_window_hours=6, max_cycle_len=4,
              structuring_window_hours=6, min_counterparties=5)


def run(sizes=(100_000, 500_000, 2_000_000)):
    rows = []
    print(f"backend: {B.name()} | {platform.processor() or platform.machine()}")
    for n in sizes:
        acc, tx = generate(n_accounts=max(n // 10, 5_000), n_tx=n, hours=72)
        analyze(tx.sample(min(len(tx), 20_000), random_state=0), acc, Params(**STRESS))  # warm-up (CUDA ctx / JIT)
        t = time.perf_counter()
        f = analyze(tx, acc, Params(**STRESS))
        wall = time.perf_counter() - t
        rows.append({"transactions": n, "edges_scored": f.n_edges_scored, "wall_s": round(wall, 2),
                     "cycles_found": len(f.rings), **f.timings_ms})
        print(json.dumps(rows[-1]))
    Path("benchmarks").mkdir(exist_ok=True)
    out = Path("benchmarks") / f"results_{'gpu' if B.GPU else 'cpu'}.json"
    out.write_text(json.dumps({"backend": B.name(), "rows": rows}, indent=2))
    print("\n| transactions | edges scored | wall (s) | cycles stage (ms) | structuring (ms) | PageRank (ms) |\n|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['transactions']:,} | {r['edges_scored']:,} | {r['wall_s']} | {r['cycles_scc_prune_plus_joins']:,.0f} | "
              f"{r['structuring_groupby']:,.0f} | {r['pagerank']:,.0f} |")


if __name__ == "__main__":
    run()
