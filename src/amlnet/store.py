"""Ledger stores. PostgreSQL + pgvector is the production target; SQLite is a zero-setup demo store with the
same logical schema (vector search done with NumPy there). LLM-generated SQL only ever goes through `query()`."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from .embeddings import EMBED_DIM, embed

ACCOUNT_COLS = ["account_id", "created_at", "country", "home_lat", "home_lon", "kyc_tier"]
TX_COLS = ["tx_id", "src", "dst", "amount", "ts", "channel", "geo_lat", "geo_lon"]
NOTE_COLS = ["note_id", "account_id", "pattern", "note", "created_at"]


def _ts(df: pd.DataFrame, cols=("created_at", "ts")) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        if c in df:
            df[c] = pd.to_datetime(df[c])
    return df


class SqliteStore:
    dialect = "sqlite"

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.conn = sqlite3.connect(path)

    def load(self, accounts: pd.DataFrame, tx: pd.DataFrame, notes: pd.DataFrame):
        a, t, n = _ts(accounts[ACCOUNT_COLS]), _ts(tx[TX_COLS]), _ts(notes[NOTE_COLS])
        for df in (a, t, n):  # ISO text timestamps sort & compare correctly in SQLite
            for c in ("created_at", "ts"):
                if c in df:
                    df[c] = df[c].dt.strftime("%Y-%m-%d %H:%M:%S")
        n["embedding"] = [json.dumps(v.round(5).tolist()) for v in embed(list(n["note"]), "passage")]
        for name, df in (("accounts", a), ("transactions", t), ("fraud_notes", n)):
            df.to_sql(name, self.conn, if_exists="replace", index=False)
        self.conn.execute("CREATE INDEX IF NOT EXISTS tx_ts ON transactions(ts)")
        self.conn.commit()

    def query(self, sql: str) -> pd.DataFrame:
        self.conn.execute("PRAGMA query_only = ON")
        return pd.read_sql_query(sql, self.conn)

    def anchor(self) -> pd.Timestamp:
        return pd.Timestamp(self.query("SELECT MAX(ts) AS m FROM transactions")["m"][0])

    def accounts(self) -> pd.DataFrame:
        return _ts(self.query("SELECT * FROM accounts"))

    def search_notes(self, query: str, k: int = 3) -> list[dict]:
        notes = self.query("SELECT note_id, pattern, note, embedding FROM fraud_notes")
        mat = np.array([json.loads(e) for e in notes["embedding"]], dtype=np.float32)
        sims = mat @ embed([query], "query")[0]
        top = np.argsort(-sims)[:k]
        return [{"note_id": int(notes.note_id[i]), "pattern": notes.pattern[i], "note": notes.note[i],
                 "similarity": round(float(sims[i]), 3)} for i in top]


class PostgresStore:
    dialect = "postgresql"

    def __init__(self, dsn: str):
        import psycopg  # optional dependency
        self.psycopg, self.dsn = psycopg, dsn

    def init_schema(self, schema_path: str | Path = "sql/schema.sql"):
        with self.psycopg.connect(self.dsn, autocommit=True) as c:
            c.execute(Path(schema_path).read_text())

    def load(self, accounts: pd.DataFrame, tx: pd.DataFrame, notes: pd.DataFrame):
        self.init_schema()
        a, t, n = _ts(accounts[ACCOUNT_COLS]), _ts(tx[TX_COLS]), _ts(notes[NOTE_COLS])
        vecs = embed(list(n["note"]), "passage")
        with self.psycopg.connect(self.dsn) as c, c.cursor() as cur:
            cur.execute("TRUNCATE transactions, fraud_notes, accounts CASCADE")
            for table, df in (("accounts", a), ("transactions", t)):  # COPY = fastest bulk path
                with cur.copy(f"COPY {table} ({','.join(df.columns)}) FROM STDIN") as cp:
                    for row in df.itertuples(index=False):
                        cp.write_row([None if pd.isna(x) else x for x in row])
            cur.executemany(
                "INSERT INTO fraud_notes (note_id, account_id, pattern, note, created_at, embedding) "
                "VALUES (%s,%s,%s,%s,%s,%s::vector)",
                [(int(r.note_id), None, r.pattern, r.note, r.created_at.to_pydatetime(),
                  "[" + ",".join(f"{x:.5f}" for x in v) + "]") for r, v in zip(n.itertuples(), vecs)])
            cur.execute("ANALYZE")

    def query(self, sql: str) -> pd.DataFrame:
        with self.psycopg.connect(self.dsn) as c:
            c.read_only = True                       # READ ONLY transaction
            with c.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '15s'")
                cur.execute(sql)
                return pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])

    def anchor(self) -> pd.Timestamp:
        return pd.Timestamp(self.query("SELECT MAX(ts) AS m FROM transactions")["m"][0])

    def accounts(self) -> pd.DataFrame:
        return self.query("SELECT * FROM accounts")

    def search_notes(self, query: str, k: int = 3) -> list[dict]:
        v = "[" + ",".join(f"{x:.5f}" for x in embed([query], "query")[0]) + "]"
        df = self.query(f"SELECT note_id, pattern, note, 1 - (embedding <=> '{v}'::vector) AS similarity "
                        f"FROM fraud_notes ORDER BY embedding <=> '{v}'::vector LIMIT {int(k)}")
        return [{**r, "similarity": round(float(r["similarity"]), 3)} for r in df.to_dict("records")]


def get_store(sqlite_path: str = ":memory:"):
    dsn = os.getenv("DATABASE_URL")
    return PostgresStore(dsn) if dsn else SqliteStore(sqlite_path)
