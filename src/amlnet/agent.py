"""Risk-analyst agent: natural language -> guarded SQL + detector parameters -> GPU graph analysis
-> structured fraud audit report.

Pipeline (each stage is auditable and independently testable):
  1. PLAN     NIM LLM turns the question into {parameters, SQL}; a deterministic rule-based planner is the
              offline fallback and the safety net if the model output is invalid.
  2. GUARD    SQL is validated (single read-only SELECT, table allow-list, row cap) before it touches the ledger.
  3. FETCH    Candidate transfers are pulled from PostgreSQL (or the SQLite demo store).
  4. ANALYZE  cuDF joins + cuGraph SCC / PageRank + iterative-join cycle search find the networks.
  5. RECALL   pgvector similarity search surfaces comparable past investigation notes.
  6. REPORT   NIM writes the executive summary (template fallback offline); every number comes from step 4.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

import pandas as pd

from . import backend as B
from .engine import Findings, Params, analyze
from .nim import NIMClient
from .sql_guard import UnsafeSQL, validate_select

REQUIRED_COLS = {"tx_id", "src", "dst", "amount", "ts"}
CANDIDATE_ROW_CAP = 5_000_000

SCHEMA_DOC = """Tables (dialect: {dialect}):
  accounts(account_id BIGINT PK, created_at TIMESTAMP, country CHAR(2), home_lat DOUBLE, home_lon DOUBLE, kyc_tier INT)
  transactions(tx_id BIGINT PK, src BIGINT -> accounts, dst BIGINT -> accounts, amount NUMERIC, ts TIMESTAMP, channel TEXT, geo_lat DOUBLE, geo_lon DOUBLE)
  fraud_notes(note_id, account_id, pattern, note TEXT, created_at, embedding vector)
"Now" is MAX(ts) of transactions (the stream's high-water mark), not the wall clock."""


@dataclass
class Plan:
    params: Params
    sql: str
    source: str                      # "nim" | "rules"
    warnings: list = field(default_factory=list)


# ----------------------------------------------------------------------------- SQL templates
def candidate_sql(dialect: str, floor: float, hours: float) -> str:
    """Time-windowed, amount-floored transfer pull joined to sender/receiver account age."""
    if dialect == "sqlite":
        age = lambda a: f"(julianday(anchor.now_ts) - julianday({a}.created_at)) * 24.0"
        since = f"datetime(anchor.now_ts, '-{hours} hours')"
    else:
        age = lambda a: f"EXTRACT(EPOCH FROM (anchor.now_ts - {a}.created_at)) / 3600.0"
        since = f"anchor.now_ts - interval '{hours} hours'"
    return (
        "WITH anchor AS (SELECT MAX(ts) AS now_ts FROM transactions) "
        "SELECT t.tx_id, t.src, t.dst, t.amount, t.ts, t.geo_lat, t.geo_lon, "
        f"{age('sa')} AS src_age_h, {age('da')} AS dst_age_h "
        "FROM transactions t CROSS JOIN anchor "
        "JOIN accounts sa ON sa.account_id = t.src JOIN accounts da ON da.account_id = t.dst "
        f"WHERE t.amount >= {floor:g} AND t.ts >= {since} ORDER BY t.ts"
    )


# ----------------------------------------------------------------------------- planners
_UNITS = {"m": 1 / 60, "h": 1, "d": 24}


def _money(txt: str) -> float:
    m = re.search(r"\$?\s*([\d][\d,]*\.?\d*)\s*([kKmM])?\b", txt)
    v = float(m.group(1).replace(",", ""))
    return v * {"k": 1e3, "m": 1e6}.get((m.group(2) or "").lower(), 1)


def rules_plan(question: str, dialect: str) -> Plan:
    q = question.lower()
    p = Params()
    m = re.search(r"(?:exceed\w*|over|above|more than|greater than|at least|>=?)\s*(\$?\s*[\d][\d,]*\.?\d*\s*[km]?)\b", q) \
        or re.search(r"(\$\s*[\d][\d,]*\.?\d*\s*[km]?)\b", q)
    if m:
        p.min_amount = _money(m.group(1))
    m = re.search(r"(?:past|last|within|previous|in the)\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?|h|days?|d)\b", q)
    if m:
        p.lookback_hours = float(m.group(1)) * _UNITS[m.group(2)[0]]
    p.cycle_window_hours = min(max(p.lookback_hours, 1), 24)
    p.structuring_window_hours = p.cycle_window_hours
    if re.search(r"new(?:ly)?[\s-]+(?:created|opened|registered)|new accounts?|recently (?:created|opened)", q):
        p.require_new_account = True
        p.new_account_hours = max(p.lookback_hours, 24) if p.lookback_hours <= 24 else p.lookback_hours
    sql = candidate_sql(dialect, min(p.min_amount, p.band_low), p.lookback_hours)
    return Plan(p, sql, "rules")


def _extract_json(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < 0:
        raise ValueError("no JSON object in model output")
    return json.loads(text[a:b + 1])


def nim_plan(question: str, dialect: str, client: NIMClient) -> Plan:
    system = (
        "You are an AML analytics planner. Convert the analyst's request into a JSON object with keys:\n"
        '  "min_amount" (number, USD), "lookback_hours" (number), "require_new_account" (bool),\n'
        '  "new_account_hours" (number, default 24), "sql" (string).\n'
        "The SQL must be ONE read-only SELECT that returns exactly these columns: "
        "tx_id, src, dst, amount, ts, src_age_h, dst_age_h (account ages in hours at 'now'), covering all "
        "transfers in the lookback window with amount >= LEAST(min_amount, 9000) so structuring just under $10k "
        "is still visible. Use only the tables below. Output JSON only, no prose.\n\n" + SCHEMA_DOC.format(dialect=dialect)
    )
    raw = client.chat([{"role": "system", "content": system}, {"role": "user", "content": question}])
    d = _extract_json(raw)
    base = rules_plan(question, dialect)           # rules supply defaults + the fallback SQL
    p = base.params
    for key in ("min_amount", "lookback_hours", "new_account_hours"):
        if isinstance(d.get(key), (int, float)) and d[key] > 0:
            setattr(p, key, float(d[key]))
    if isinstance(d.get("require_new_account"), bool):
        p.require_new_account = d["require_new_account"]
    p.cycle_window_hours = min(max(p.lookback_hours, 1), 24)
    p.structuring_window_hours = p.cycle_window_hours
    return Plan(p, d.get("sql", ""), "nim")


# ----------------------------------------------------------------------------- agent
class AMLAgent:
    def __init__(self, store, nim: NIMClient | None | str = "env"):
        self.store = store
        self.nim = NIMClient.from_env() if nim == "env" else nim

    def plan(self, question: str) -> Plan:
        warnings = []
        if self.nim is not None:
            try:
                plan = nim_plan(question, self.store.dialect, self.nim)
                plan.sql = validate_select(plan.sql, CANDIDATE_ROW_CAP)
                return plan
            except (UnsafeSQL, ValueError, KeyError, OSError) as exc:
                warnings.append(f"NIM plan rejected ({type(exc).__name__}: {exc}); used rule-based planner")
        plan = rules_plan(question, self.store.dialect)
        plan.sql = validate_select(plan.sql, CANDIDATE_ROW_CAP)
        plan.warnings = warnings
        return plan

    def _fetch(self, plan: Plan) -> pd.DataFrame:
        try:
            cand = self.store.query(plan.sql)
            if not REQUIRED_COLS <= set(cand.columns):
                raise ValueError(f"missing columns {REQUIRED_COLS - set(cand.columns)}")
        except Exception as exc:
            if plan.source != "nim":
                raise
            plan.warnings.append(f"NIM SQL failed ({exc}); re-ran with template SQL")
            plan.sql = validate_select(candidate_sql(self.store.dialect, min(plan.params.min_amount, plan.params.band_low),
                                                     plan.params.lookback_hours), CANDIDATE_ROW_CAP)
            plan.source = "nim+rules"
            cand = self.store.query(plan.sql)
        cand["amount"] = cand["amount"].astype(float)
        cand["ts"] = pd.to_datetime(cand["ts"])
        return cand

    def investigate(self, question: str) -> dict:
        plan = self.plan(question)
        cand = self._fetch(plan)
        findings = analyze(cand, self.store.accounts(), plan.params, anchor=self.store.anchor())
        amount_of = dict(zip(cand["tx_id"], cand["amount"]))
        for r in findings.rings:
            r["total_flow"] = round(sum(amount_of.get(t, 0) for t in r["tx_ids"]), 2)
        notes = self._recall(findings)
        report = {
            "question": question,
            "as_of": findings.anchor,
            "plan": {"source": plan.source, "parameters": asdict(plan.params), "sql": plan.sql, "warnings": plan.warnings},
            "metrics": _metrics(findings),
            "rings": findings.rings,
            "structuring": findings.structuring,
            "pass_through_accounts": findings.mules,
            "high_risk_accounts": findings.accounts[:15],
            "similar_past_cases": notes,
            "recommended_actions": _actions(findings),
            "performance": {"backend": findings.backend, "candidate_transfers": int(len(cand)),
                            "transfers_in_window": findings.n_tx_window, "timings_ms": findings.timings_ms},
        }
        report["summary"] = self._summarize(question, report)
        return report

    # ---- pgvector recall ---------------------------------------------------------------
    def _recall(self, f: Findings) -> list[dict]:
        probes = []
        if f.rings:
            probes.append("circular flow of funds across newly opened accounts layering cross-border")
        if f.structuring:
            probes.append("structuring many deposits just under the $10,000 reporting threshold consolidated")
        if f.mules:
            probes.append("new account pass-through mule received funds and forwarded within minutes")
        seen, out = set(), []
        for q in probes:
            for n in self.store.search_notes(q, k=2):
                if n["note_id"] not in seen:
                    seen.add(n["note_id"])
                    out.append(n)
        return out

    # ---- narrative -------------------------------------------------------------------------
    def _summarize(self, question: str, report: dict) -> str:
        m = report["metrics"]
        template = (
            f"In the analysis window ending {report['as_of']}, {m['accounts_flagged']} accounts crossed the risk "
            f"threshold. The graph engine found {m['circular_flow_networks']} circular-flow network(s) "
            f"(${m['ring_value_usd']:,.0f} cycled), {m['structuring_clusters']} structuring cluster(s) "
            f"(${m['structured_value_usd']:,.0f} moved in just-under-threshold transfers) and "
            f"{m['pass_through_accounts']} additional new-account pass-through account(s)."
        )
        if self.nim is None:
            return template
        try:
            slim = {k: report[k] for k in ("metrics", "recommended_actions")}
            slim["top_accounts"] = [{"account": a["account"], "score": a["score"], "reasons": a["reasons"][:2]}
                                    for a in report["high_risk_accounts"][:5]]
            return self.nim.chat([
                {"role": "system", "content": "You are a senior financial-crimes analyst. Write a 3-4 sentence executive "
                 "summary for an audit report. Use ONLY the numbers in the JSON; never invent facts."},
                {"role": "user", "content": f"Request: {question}\nFindings JSON: {json.dumps(slim, default=str)}"}],
                max_tokens=300).strip()
        except Exception:
            return template


def _metrics(f: Findings) -> dict:
    return {
        "accounts_flagged": len(f.flagged()),
        "circular_flow_networks": len(f.rings),
        "ring_value_usd": round(sum(r["principal"] for r in f.rings), 2),
        "structuring_clusters": len(f.structuring),
        "structured_value_usd": round(sum(s["total"] for s in f.structuring), 2),
        "pass_through_accounts": len(f.mules),
        "cross_border_ring_hops": sum(r["cross_border_hops"] for r in f.rings),
    }


def _actions(f: Findings) -> list[str]:
    acts = []
    if f.rings:
        acts.append("Place temporary holds on all members of each circular-flow network; open a linked case and "
                    "evaluate a SAR filing for layering.")
    if f.structuring:
        acts.append("Aggregate each structuring cluster's deposits for CTR/SAR analysis; review feeder-account onboarding "
                    "(device, KYC data reuse) and follow forwarded funds to downstream beneficiaries.")
    if f.mules:
        acts.append("Contact the receiving institutions to recall funds from pass-through accounts; check the sending "
                    "accounts for authorized-push-payment scam victims.")
    if any(r["cross_border_hops"] for r in f.rings):
        acts.append("Escalate cross-border legs for sanctions / high-risk-jurisdiction screening.")
    return acts or ["No networks met the thresholds; no action required."]
