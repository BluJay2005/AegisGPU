-- Ledger + investigation memory in ONE database: relational joins and vector search side by side.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS accounts (
    account_id  BIGINT PRIMARY KEY,
    created_at  TIMESTAMP NOT NULL,
    country     CHAR(2)   NOT NULL,
    home_lat    DOUBLE PRECISION,
    home_lon    DOUBLE PRECISION,
    kyc_tier    SMALLINT  NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_id    BIGINT PRIMARY KEY,
    src      BIGINT NOT NULL REFERENCES accounts(account_id),
    dst      BIGINT NOT NULL REFERENCES accounts(account_id),
    amount   NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    ts       TIMESTAMP NOT NULL,
    channel  TEXT,
    geo_lat  DOUBLE PRECISION,
    geo_lon  DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS tx_ts_idx     ON transactions (ts);
CREATE INDEX IF NOT EXISTS tx_src_idx    ON transactions (src);
CREATE INDEX IF NOT EXISTS tx_dst_idx    ON transactions (dst);
CREATE INDEX IF NOT EXISTS tx_amount_idx ON transactions (amount);

-- Past investigation notes, embedded for semantic "have we seen this before?" lookups.
CREATE TABLE IF NOT EXISTS fraud_notes (
    note_id     BIGINT PRIMARY KEY,
    account_id  BIGINT,
    pattern     TEXT,
    note        TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL,
    embedding   vector(1024) NOT NULL
);
CREATE INDEX IF NOT EXISTS fraud_notes_hnsw ON fraud_notes USING hnsw (embedding vector_cosine_ops);
