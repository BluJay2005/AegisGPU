import pytest

from amlnet.sql_guard import UnsafeSQL, validate_select


@pytest.mark.parametrize("sql", [
    "DROP TABLE transactions", "DELETE FROM transactions", "SELECT 1; DROP TABLE accounts",
    "UPDATE accounts SET country='US'", "SELECT * FROM pg_user", "SELECT * FROM transactions JOIN secrets ON 1=1",
    "WITH x AS (SELECT 1) INSERT INTO accounts SELECT * FROM x", "SELECT pg_sleep(100)",
    "SELECT * FROM transactions -- hi\n; DROP TABLE x", "COPY transactions TO '/tmp/x'", "",
])
def test_rejects_unsafe(sql):
    with pytest.raises(UnsafeSQL):
        validate_select(sql)


def test_accepts_and_caps_rows():
    assert validate_select("SELECT * FROM transactions;").endswith("LIMIT 500")
    assert validate_select("SELECT * FROM transactions LIMIT 99999", 100).endswith("LIMIT 100")
    assert validate_select("SELECT * FROM transactions LIMIT 7").endswith("LIMIT 7")


def test_accepts_ctes_extract_and_string_literals():
    validate_select("WITH a AS (SELECT MAX(ts) m FROM transactions) SELECT EXTRACT(EPOCH FROM a.m - t.ts) "
                    "FROM transactions t CROSS JOIN a WHERE t.channel = 'drop table'")
