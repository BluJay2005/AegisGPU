import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from amlnet.agent import AMLAgent, rules_plan
from amlnet.nim import NIMClient


def test_rules_planner_parses_the_headline_question():
    p = rules_plan("Synthesize suspicious transfer networks exceeding $10k routed through newly created "
                   "accounts in the past 6 hours.", "sqlite").params
    assert p.min_amount == 10_000 and p.lookback_hours == 6 and p.require_new_account


@pytest.mark.parametrize("q,amt,hrs", [("transfers over $25,000 in the last 2 days", 25_000, 48),
                                       ("anything above 50k within 90 minutes", 50_000, 1.5)])
def test_rules_planner_units(q, amt, hrs):
    p = rules_plan(q, "sqlite").params
    assert (p.min_amount, p.lookback_hours) == (amt, hrs)


def test_offline_agent_report(store):
    rep = AMLAgent(store, nim=None).investigate("Find rings over $10k in the past 6 hours")
    assert rep["plan"]["source"] == "rules" and rep["rings"] and rep["similar_past_cases"]
    assert rep["metrics"]["accounts_flagged"] > 0 and "SELECT" in rep["plan"]["sql"]


class FakeNIM(NIMClient):
    """Stands in for the model so tests need no network."""
    def __init__(self, reply):
        super().__init__("k", "http://x", "m", "e")
        self.reply = reply

    def chat(self, messages, **kw):
        return self.reply


def test_agent_uses_model_sql_when_valid(store):
    good_sql = ("WITH anchor AS (SELECT MAX(ts) AS now_ts FROM transactions) SELECT t.tx_id, t.src, t.dst, t.amount, t.ts, "
                "(julianday(anchor.now_ts)-julianday(sa.created_at))*24.0 AS src_age_h, "
                "(julianday(anchor.now_ts)-julianday(da.created_at))*24.0 AS dst_age_h "
                "FROM transactions t CROSS JOIN anchor JOIN accounts sa ON sa.account_id=t.src "
                "JOIN accounts da ON da.account_id=t.dst WHERE t.amount >= 9000 AND t.ts >= datetime(anchor.now_ts,'-6 hours')")
    reply = "```json\n" + json.dumps({"min_amount": 10000, "lookback_hours": 6, "require_new_account": False, "sql": good_sql}) + "\n```"
    rep = AMLAgent(store, nim=FakeNIM(reply)).investigate("rings please")
    assert rep["plan"]["source"] == "nim" and rep["rings"]


@pytest.mark.parametrize("bad_reply", [
    json.dumps({"sql": "DROP TABLE transactions"}),                       # destructive
    json.dumps({"sql": "SELECT * FROM transactions JOIN secrets ON 1=1"}),  # unknown table
    "I'm sorry, I can't help with that.",                                 # no JSON
])
def test_agent_falls_back_when_model_output_is_unsafe_or_garbage(store, bad_reply):
    rep = AMLAgent(store, nim=FakeNIM(bad_reply)).investigate("rings over $10k past 6 hours")
    assert rep["plan"]["source"] == "rules" and rep["plan"]["warnings"] and rep["rings"]


def test_model_sql_missing_columns_triggers_template_rerun(store):
    reply = json.dumps({"sql": "SELECT tx_id, src FROM transactions"})
    rep = AMLAgent(store, nim=FakeNIM(reply)).investigate("rings over $10k past 6 hours")
    assert rep["plan"]["source"] == "nim+rules" and rep["rings"]


def test_nim_http_client_against_fake_openai_server():
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers["Authorization"] == "Bearer secret"
            out = ({"choices": [{"message": {"content": "pong"}}]} if self.path.endswith("/chat/completions")
                   else {"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]})
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps(out).encode())
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = NIMClient("secret", f"http://127.0.0.1:{srv.server_port}/v1", "m", "e")
    assert c.chat([{"role": "user", "content": "ping"}]) == "pong"
    assert c.embed(["a", "b"]) == [[1, 0], [0, 1]]   # re-ordered by index
    srv.shutdown()
