# Real-Time Fraud & AML Network Analyzer

GPU-accelerated detection of money-laundering **networks** (not just single bad transactions), with a natural-language
risk-analyst agent that turns a question like

> *"Synthesize suspicious transfer networks exceeding $10k routed through newly created accounts in the past 6 hours."*

into guarded SQL, GPU graph analysis, and a structured, auditable fraud report.

![Flagged networks](docs/network.png)
<sub>Output of `make demo`: 145 accounts. Red = circular flow (layering), orange/dark red = structuring feeders and hub, purple = collector, blue = new-account mule, grey = unflagged counterparties.</sub>

## The problem

Legacy batch jobs score transactions hours after the fact, one row at a time. Organised fraud lives in the *relationships*:
money cycling through brand-new accounts, dozens of "just-under-$10k" deposits funnelled into one account, victim funds
bounced through a mule within minutes. Those shapes are invisible to row-level rules and expensive to find with
CPU-side graph code at ledger scale.

## What it detects

| Typology | How it is found | Technique |
|---|---|---|
| **Circular flows / layering** | Loops of 3–6 accounts, each hop later in time than the last, forwarding 75–100% of the previous amount, completing inside a time window | SCC pruning (cuGraph) → iterative self-joins (cuDF) |
| **Structuring / smurfing** | ≥5 distinct counterparties moving $9,000–$9,999 into (or out of) one account in a short window, plus where the hub forwarded the money | grouped aggregation with `nunique` |
| **New-account mules** | Account younger than N hours receives ≥ threshold and forwards ≥85% within 2 h | join + groupby on in/out flows |
| **Geographic context** | Cross-border hops and great-circle distance per ring | sender/receiver join + haversine |

Each account gets an **explainable additive risk score** (every point of score has a human-readable reason), not a black box.

## Architecture

```mermaid
flowchart LR
  Q["Analyst question<br/>(natural language)"] --> P["NIM LLM planner<br/>→ params + SQL"]
  P --> G{"SQL guard<br/>1 read-only SELECT<br/>table allow-list, row cap"}
  G -- rejected / invalid --> R["Rule-based planner<br/>(deterministic fallback)"]
  G -- ok --> L[("PostgreSQL<br/>ledger + pgvector notes")]
  R --> L
  L -- "candidate transfers" --> E["RAPIDS engine<br/>cuDF joins · cuGraph SCC/PageRank<br/>cycle search · structuring · mules"]
  E --> S["Explainable risk scores"]
  S --> V["pgvector recall:<br/>similar past investigations"]
  S --> A["NIM LLM: executive summary<br/>(numbers only from engine)"]
  V --> O["Audit report<br/>Markdown + JSON + SQL audit trail"]
  A --> O