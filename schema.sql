-- MySQL / MariaDB schema.  Run once:  python app_schema.py
-- Every statement ends with a semicolon and comments never contain one.
-- utf8mb4_bin = exact (case-sensitive) matching, so duplicates behave like the old Postgres setup.

CREATE TABLE IF NOT EXISTS cases (
    court_code       VARCHAR(16)   NOT NULL,
    case_id          VARCHAR(64)   NOT NULL,
    party_name       VARCHAR(255)  NOT NULL DEFAULT '',
    party_type       VARCHAR(64)   NOT NULL DEFAULT '',
    case_title       TEXT          NOT NULL,
    filed_date       VARCHAR(64)   NOT NULL DEFAULT '',
    case_status      VARCHAR(128)  NOT NULL DEFAULT '',
    balance          VARCHAR(64)   NOT NULL DEFAULT '',
    court            VARCHAR(255)  NOT NULL DEFAULT '',
    year_of_birth    VARCHAR(16)   NOT NULL DEFAULT '',
    search_last_name VARCHAR(100)  NOT NULL,
    page_number      INT           NOT NULL,
    scraped_at       TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (court_code, case_id, party_name, party_type)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS name_summary (
    court_code    VARCHAR(16)  NOT NULL,
    last_name     VARCHAR(100) NOT NULL,
    total_records INT          NOT NULL,
    total_pages   INT          NOT NULL,
    status        VARCHAR(16)  NOT NULL,
    counted_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (court_code, last_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS scraped_pages (
    court_code VARCHAR(16)  NOT NULL,
    last_name  VARCHAR(100) NOT NULL,
    page       INT          NOT NULL,
    case_count INT          NOT NULL,
    scraped_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (court_code, last_name, page)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS failed_tasks (
    id        BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_key  VARCHAR(400) NOT NULL,
    payload   TEXT         NOT NULL,
    error     TEXT,
    attempts  INT,
    failed_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE OR REPLACE VIEW v_name_progress AS
SELECT s.court_code, s.last_name, s.total_records, s.total_pages,
       COALESCE(SUM(p.case_count), 0) AS scraped_records,
       COUNT(p.page)                  AS scraped_pages
FROM name_summary s
LEFT JOIN scraped_pages p ON p.court_code = s.court_code AND p.last_name = s.last_name
GROUP BY s.court_code, s.last_name, s.total_records, s.total_pages;
