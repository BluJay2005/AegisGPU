"""Guardrails for LLM-generated SQL. The model is untrusted: it may only run one read-only SELECT
over an allow-list of tables, always with a row cap. (Defense in depth: Postgres also runs it in a
READ ONLY transaction with a statement timeout.)"""
from __future__ import annotations

import re

ALLOWED_TABLES = {"accounts", "transactions", "fraud_notes"}
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|call|do|execute|merge|vacuum|"
    r"attach|detach|pragma|set|reset|listen|notify|lock|pg_sleep|pg_read_file|lo_import|dblink)\b", re.I)


class UnsafeSQL(ValueError):
    pass


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def validate_select(sql: str, max_rows: int = 500) -> str:
    """Return a sanitized, row-capped version of `sql` or raise UnsafeSQL."""
    s = _strip_comments(sql).strip().rstrip(";").strip()
    if not s:
        raise UnsafeSQL("empty query")
    if ";" in s:
        raise UnsafeSQL("multiple statements are not allowed")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise UnsafeSQL("only SELECT / WITH queries are allowed")
    bad = _FORBIDDEN.search(re.sub(r"'[^']*'", "''", s))  # ignore keywords inside string literals
    if bad:
        raise UnsafeSQL(f"forbidden keyword: {bad.group(1).lower()}")
    # FROM inside EXTRACT(...) / IS DISTINCT FROM is not a table reference; drop schema prefix
    s_scan = re.sub(r"\bextract\s*\(\s*\w+\s+from\b", "extract(", s, flags=re.I)
    s_scan = re.sub(r"\bdistinct\s+from\b", "<>", s_scan, flags=re.I)
    s_scan = re.sub(r"\bpublic\.", "", s_scan, flags=re.I)
    ctes = set(re.findall(r"\b(\w+)\s+as\s*\(", s_scan, re.I))
    tables = set(t.lower() for t in re.findall(r"\b(?:from|join)\s+([A-Za-z_][\w\.]*)", s_scan, re.I))
    illegal = {t for t in tables if t not in ALLOWED_TABLES and t not in {c.lower() for c in ctes}}
    if illegal:
        raise UnsafeSQL(f"table(s) not allowed: {', '.join(sorted(illegal))}")
    m = re.search(r"\blimit\s+(\d+)\s*$", s, re.I)
    if m:
        if int(m.group(1)) > max_rows:
            s = s[:m.start()] + f"LIMIT {max_rows}"
    else:
        s += f" LIMIT {max_rows}"
    return s
