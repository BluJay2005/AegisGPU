# Real Time Fraud & AML Network Analyzer

A GPU accelerated engine that finds organized **money laundering networks** (not just single bad transactions). It includes a natural language analyst assistant that turns questions like:

> *"Find me suspicious transfer networks exceeding $10k routed through newly created accounts in the past 6 hours."*

into safe, read only database queries, GPU graph analysis, and a structured, auditable fraud report.

![Flagged networks](docs/network.png)

### 💡 How to Read This Network Graph

In this graph, **each circle (node) is a bank account** and **each arrow (edge) is a money transfer** moving from the sender to the receiver. 

When you run `make demo`, the detection engine analyzes 145 accounts and colors them based on the role they play in the suspicious network:

- 🔴 **Red (Circular Flow / Layering):** Accounts passing money in a closed loop (e.g., Account A → B → C → A) to disguise where the funds originated.
- 🟠 / 🟤 **Orange & Dark Red (Structuring Feeder / Hub):** Accounts involved in "smurfing"—dozens of small deposits under $10k funneling into one central account to avoid reporting limits.
- 🟣 **Purple (Collector Account):** A destination account pooling funds gathered from multiple feeder rings.
- 🔵 **Blue (New-Account Mule):** A recently opened account that receives a high value transfer and immediately forwards almost all of it out within 2 hours.
- ⚪ **Grey (Unflagged Counterparties):** Normal accounts connected to the transaction network that showed no suspicious patterns.

**Why this matters:** A single row in a spreadsheet only shows "Account A sent $9,500 to Account B." Viewing the data as a visual graph immediately exposes that Account B is actually part of an organized 10 account laundering ring.

---

## What Problem Does This Solve?

Standard anti money laundering (AML) software screens transactions one by one like flagging a single transfer over $10,000. 

However, organized financial crime happens across **relationships between multiple accounts**:
- Bouncing money in loops across several new accounts to hide its origin.
- Breaking a large cash sum into dozens of deposits under $10,000 ("smurfing") into a central account.
- Passing victim funds through a brand new "mule" account within minutes.

Checking these complex relationships across millions of transactions is slow on standard CPUs. This project uses **GPU graph processing** to trace these network patterns instantly.

---

## What It Detects (Fraud Typologies)

| Fraud Pattern | What Is Happening | How It Is Found |
|---|---|---|
| **Circular Flows (Layering)** | Money moves in a loop (e.g., Account A → B → C → A), where each step happens later in time and forwards most of the money. | Relational joins with strictly increasing timestamps to trace cycles. |
| **Structuring (Smurfing)** | 5+ senders move $9,000–$9,999 into one central account in a short window to avoid legal reporting limits. | Grouped aggregations counting unique counterparties per hub. |
| **New-Account Mules** | An account opened recently receives a large transfer and forwards 85%+ of the money within 2 hours. | Flow tracking comparing incoming vs. outgoing transaction times. |
| **Geographic Context** | Distance between senders and receivers across cross-border transfers. | Geographic coordinate joins using haversine distance. |

Every flagged account receives an **explainable risk score** where every point is tied to a specific human readable reason (e.g., `+50: Part of a 4-hop circular loop`), rather than an opaque black box AI score.

---

## System Architecture

```mermaid
flowchart LR
  Q["Analyst question<br/>(natural language)"] --> P["LLM Planner<br/>(NVIDIA NIM)"]
  P --> G{"SQL Guard<br/>Read-only SELECT check"}
  G -- Rejected / Invalid --> R["Rule-Based Planner<br/>(Deterministic Fallback)"]
  G -- Approved --> L[("PostgreSQL Ledger<br/>+ pgvector Notes")]
  R --> L
  L -- "Candidate Transfers" --> E["Graph Engine<br/>GPU (cuDF/cuGraph) or CPU (Pandas/NetworkX)"]
  E --> S["Explainable Risk Scores"]
  S --> V["pgvector Recall:<br/>Similar Past Cases"]
  S --> A["LLM Executive Summary<br/>(Uses exact numbers from engine)"]
  V --> O["Audit Report<br/>Markdown + JSON + SQL Trail"]
  A --> O