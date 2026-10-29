"""Render an investigation report dict as an audit-ready Markdown document (JSON is the system of record)."""
from __future__ import annotations

import json
from pathlib import Path


def _ids(xs, n=8):
    xs = list(xs)
    return ", ".join(f"`{x}`" for x in xs[:n]) + (f" … (+{len(xs) - n})" if len(xs) > n else "")


def render_markdown(r: dict) -> str:
    m, perf, plan = r["metrics"], r["performance"], r["plan"]
    L = [f"# Fraud & AML Network Audit Report", "",
         f"**Request:** {r['question']}  ", f"**Data as of:** {r['as_of']}  ",
         f"**Planner:** {plan['source']}  |  **Engine:** {perf['backend']}", "",
         "## Executive summary", "", r["summary"], "",
         "## Key metrics", "", "| Metric | Value |", "|---|---|",
         f"| Accounts flagged (score ≥ {plan['parameters']['flag_threshold']}) | {m['accounts_flagged']} |",
         f"| Circular-flow networks | {m['circular_flow_networks']} (${m['ring_value_usd']:,.0f} principal) |",
         f"| Structuring clusters | {m['structuring_clusters']} (${m['structured_value_usd']:,.0f}) |",
         f"| Pass-through accounts | {m['pass_through_accounts']} |",
         f"| Cross-border ring hops | {m['cross_border_ring_hops']} |", ""]
    if r["rings"]:
        L += ["## Circular money flows (layering)", "",
              "| Ring | Accounts | Principal | Total flow | Duration | Countries | New accts | Transfers |",
              "|---|---|---|---|---|---|---|---|"]
        for x in r["rings"]:
            L.append(f"| {x['ring_id']} | {_ids(x['accounts'])} | ${x['principal']:,.0f} | ${x.get('total_flow', 0):,.0f} | "
                     f"{x['duration_min']:.0f} min | {', '.join(x['countries'])} | {len(x['new_accounts'])}/{x['n_accounts']} | "
                     f"{_ids(x['tx_ids'], 4)} |")
        L.append("")
    if r["structuring"]:
        L += ["## Structuring (just under the $10k threshold)", "",
              "| Cluster | Hub | Direction | Counterparties | Total | Window | Forwarded to |", "|---|---|---|---|---|---|---|"]
        for x in r["structuring"]:
            L.append(f"| {x['cluster_id']} | `{x['hub']}` | {x['kind']} | {x['n_counterparties']} | ${x['total']:,.0f} | "
                     f"{x['start'][11:16]}–{x['end'][11:16]} | {_ids(x['forwarded_to']) or '—'} |")
        L.append("")
    if r["pass_through_accounts"]:
        L += ["## New-account pass-through", "", "| Account | Age (h) | In | Out | Forwarded after |", "|---|---|---|---|---|"]
        for x in r["pass_through_accounts"]:
            L.append(f"| `{x['account']}` | {x['age_h']} | ${x['in_total']:,.0f} | ${x['out_total']:,.0f} | {x['delay_min']} min |")
        L.append("")
    L += ["## Highest-risk accounts", "", "| Account | Score | Why |", "|---|---|---|"]
    for a in r["high_risk_accounts"][:10]:
        L.append(f"| `{a['account']}` | {a['score']} | {'; '.join(a['reasons'])} |")
    L.append("")
    if r["similar_past_cases"]:
        L += ["## Similar past investigations (pgvector semantic search)", ""]
        for n in r["similar_past_cases"]:
            L.append(f"- **{n['pattern']}** (similarity {n['similarity']}): {n['note']}")
        L.append("")
    L += ["## Recommended actions", ""] + [f"{i}. {a}" for i, a in enumerate(r["recommended_actions"], 1)] + [""]
    L += ["## Audit trail", "", f"Parameters: `{json.dumps(plan['parameters'])}`", "", "```sql", plan["sql"], "```"]
    if plan["warnings"]:
        L += ["", "**Planner warnings:** " + "; ".join(plan["warnings"])]
    L += ["", f"Timings (ms): `{json.dumps(perf['timings_ms'])}` — {perf['transfers_in_window']:,} transfers in window."]
    return "\n".join(L) + "\n"


def save(report: dict, out_dir: str | Path = "reports", stem: str = "audit_report") -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / f"{stem}.md", out / f"{stem}.json"
    md.write_text(render_markdown(report))
    js.write_text(json.dumps(report, indent=2, default=str))
    return md, js
