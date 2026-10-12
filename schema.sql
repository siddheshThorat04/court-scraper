-- Run once:  psql "$DATABASE_URL" -f schema.sql
-- Lean on purpose (Neon free tier = 0.5 GB/project): one index per table,
-- no surrogate id, no URL column.

CREATE TABLE IF NOT EXISTS cases (
    court_code       TEXT        NOT NULL,
    case_id          TEXT        NOT NULL,
    party_name       TEXT        NOT NULL DEFAULT '',
    party_type       TEXT        NOT NULL DEFAULT '',
    case_title       TEXT        NOT NULL DEFAULT '',
    filed_date       TEXT        NOT NULL DEFAULT '',   -- kept as shown on site; convert later
    case_status      TEXT        NOT NULL DEFAULT '',
    balance          TEXT        NOT NULL DEFAULT '',
    court            TEXT        NOT NULL DEFAULT '',
    year_of_birth    TEXT        NOT NULL DEFAULT '',
    search_last_name TEXT        NOT NULL,
    page_number      INT         NOT NULL,
    scraped_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- This key makes every insert idempotent: re-delivered tasks never duplicate rows.
    PRIMARY KEY (court_code, case_id, party_name, party_type)
);

-- One row per searched name prefix: how many records the site says exist.
CREATE TABLE IF NOT EXISTS name_summary (
    court_code    TEXT        NOT NULL,
    last_name     TEXT        NOT NULL,
    total_records INT         NOT NULL,
    total_pages   INT         NOT NULL,
    status        TEXT        NOT NULL,                 -- counted | empty
    counted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (court_code, last_name)
);

-- One row per page successfully scraped (used to verify completeness).
CREATE TABLE IF NOT EXISTS scraped_pages (
    court_code TEXT        NOT NULL,
    last_name  TEXT        NOT NULL,
    page       INT         NOT NULL,
    case_count INT         NOT NULL,
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (court_code, last_name, page)
);

-- Tasks that failed every retry (mirror of the Redis dead-letter stream, durable).
CREATE TABLE IF NOT EXISTS failed_tasks (
    id        BIGSERIAL PRIMARY KEY,
    task_key  TEXT        NOT NULL,
    payload   JSONB       NOT NULL,
    error     TEXT,
    attempts  INT,
    failed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Quick completeness check:  SELECT * FROM v_name_progress WHERE scraped_pages < total_pages;
CREATE OR REPLACE VIEW v_name_progress AS
SELECT s.court_code, s.last_name, s.total_records, s.total_pages,
       COALESCE(SUM(p.case_count), 0) AS scraped_records,
       COUNT(p.page)                  AS scraped_pages
FROM name_summary s
LEFT JOIN scraped_pages p USING (court_code, last_name)
GROUP BY s.court_code, s.last_name, s.total_records, s.total_pages;
