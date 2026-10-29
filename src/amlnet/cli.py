"""amlnet command line interface."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from . import backend as B
from .agent import AMLAgent
from .engine import Params, analyze, evaluate
from .generator import generate, make_notes
from .report import render_markdown, save
from .store import SqliteStore, get_store

DEFAULT_QUESTION = ("Synthesize suspicious transfer networks exceeding $10k routed through newly created "
                    "accounts in the past 6 hours.")


def _load_csv(d: str):
    d = Path(d)
    a = pd.read_csv(d / "accounts.csv", parse_dates=["created_at"], keep_default_na=False, na_values=[])
    t = pd.read_csv(d / "transactions.csv", parse_dates=["ts"], keep_default_na=False, na_values=[])
    n = pd.read_csv(d / "notes.csv", parse_dates=["created_at"])
    return a, t, n


def cmd_generate(a):
    acc, tx = generate(a.accounts, a.tx, seed=a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    acc.to_csv(out / "accounts.csv", index=False)
    tx.to_csv(out / "transactions.csv", index=False)
    make_notes().to_csv(out / "notes.csv", index=False)
    print(f"wrote {len(acc):,} accounts, {len(tx):,} transactions -> {out}/")


def cmd_detect(a):
    acc, tx, _ = _load_csv(a.data)
    f = analyze(tx, acc, Params(lookback_hours=a.hours))
    print(f"backend: {f.backend}\nrings={len(f.rings)} structuring={len(f.structuring)} pass_through={len(f.mules)}")
    print("timings_ms:", json.dumps(f.timings_ms))
    print("evaluation vs ground truth:", json.dumps(evaluate(f, acc), indent=2))


def cmd_load(a):
    acc, tx, notes = _load_csv(a.data)
    store = get_store(a.db)
    store.load(acc, tx, notes)
    print(f"loaded {len(tx):,} transactions into {type(store).__name__} ({a.db if store.dialect == 'sqlite' else 'DATABASE_URL'})")


def cmd_ask(a):
    agent = AMLAgent(get_store(a.db))
    rep = agent.investigate(a.question)
    md, js = save(rep, a.out)
    print(render_markdown(rep))
    print(f"\nsaved: {md}  {js}")


def cmd_demo(a):
    """Everything in one command: generate -> load -> ask -> evaluate -> picture."""
    from .viz import draw
    t0 = time.perf_counter()
    acc, tx = generate(a.accounts, a.tx)
    store = SqliteStore(":memory:")
    store.load(acc, tx, make_notes())
    agent = AMLAgent(store)
    rep = agent.investigate(a.question)
    md, js = save(rep, a.out)
    print(render_markdown(rep))
    f = analyze(tx, acc)  # default full-window run for the ground-truth evaluation + picture
    print("Ground-truth evaluation (synthetic labels):", json.dumps(evaluate(f, acc)))
    Path("docs").mkdir(exist_ok=True)
    draw(f, tx, "docs/network.png")
    print(f"\nsaved: {md}, {js}, docs/network.png   (total {time.perf_counter() - t0:.1f}s, backend: {B.name()})")


def cmd_bench(a):
    from benchmarks.benchmark import run
    run(a.sizes)


def _load_dotenv(path: str = ".env"):
    """Tiny .env reader (no dependency); real environment variables win."""
    import os
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main(argv=None):
    _load_dotenv()
    p = argparse.ArgumentParser("amlnet", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write synthetic accounts/transactions/notes CSVs")
    g.add_argument("--accounts", type=int, default=20_000); g.add_argument("--tx", type=int, default=200_000)
    g.add_argument("--seed", type=int, default=7); g.add_argument("--out", default="data"); g.set_defaults(fn=cmd_generate)
    d = sub.add_parser("detect", help="run detectors on CSVs and score against ground truth")
    d.add_argument("--data", default="data"); d.add_argument("--hours", type=float, default=6); d.set_defaults(fn=cmd_detect)
    l = sub.add_parser("load", help="load CSVs into Postgres (DATABASE_URL) or a SQLite file")
    l.add_argument("--data", default="data"); l.add_argument("--db", default="data/ledger.db"); l.set_defaults(fn=cmd_load)
    k = sub.add_parser("ask", help="ask the risk-analyst agent a question")
    k.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    k.add_argument("--db", default="data/ledger.db"); k.add_argument("--out", default="reports"); k.set_defaults(fn=cmd_ask)
    o = sub.add_parser("demo", help="zero-setup end-to-end demo (in-memory)")
    o.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    o.add_argument("--accounts", type=int, default=20_000); o.add_argument("--tx", type=int, default=200_000)
    o.add_argument("--out", default="reports"); o.set_defaults(fn=cmd_demo)
    b = sub.add_parser("bench", help="stage-level benchmark across dataset sizes")
    b.add_argument("--sizes", type=int, nargs="+", default=[100_000, 500_000, 2_000_000]); b.set_defaults(fn=cmd_bench)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
