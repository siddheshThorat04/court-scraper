"""Neon/Postgres access. One transaction per flush; every insert is idempotent."""
import json
import logging

import psycopg

import config

log = logging.getLogger("db")

SQL_CASE = """
INSERT INTO cases (court_code, case_id, party_name, party_type, case_title, filed_date,
                   case_status, balance, court, year_of_birth, search_last_name, page_number)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT DO NOTHING
"""

SQL_PAGE = """
INSERT INTO scraped_pages (court_code, last_name, page, case_count) VALUES (%s,%s,%s,%s)
ON CONFLICT (court_code, last_name, page)
DO UPDATE SET case_count = EXCLUDED.case_count, scraped_at = now()
"""

SQL_SUMMARY = """
INSERT INTO name_summary (court_code, last_name, total_records, total_pages, status)
VALUES (%s,%s,%s,%s,%s)
ON CONFLICT (court_code, last_name)
DO UPDATE SET total_records = EXCLUDED.total_records, total_pages = EXCLUDED.total_pages,
              status = EXCLUDED.status, counted_at = now()
"""


class DB:
    def __init__(self, url=None):
        self.url = url or config.DATABASE_URL
        self.conn = None

    def _connect(self):
        # prepare_threshold=None: safe with Neon's pooled (PgBouncer) endpoint.
        self.conn = psycopg.connect(self.url, autocommit=False, prepare_threshold=None, connect_timeout=20)

    def ensure(self):
        if self.conn is None or self.conn.closed:
            self._connect()
        return self.conn

    def reset(self):
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception:
            pass
        self.conn = None

    def write(self, cases, pages, summaries):
        """Insert a whole batch atomically. Returns the number of NEW case rows saved."""
        conn = self.ensure()
        inserted = 0
        try:
            with conn.cursor() as cur:
                if summaries:
                    cur.executemany(SQL_SUMMARY, summaries)
                if cases:
                    cur.executemany(SQL_CASE, cases)
                    inserted = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                if pages:
                    cur.executemany(SQL_PAGE, pages)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        return inserted

    def record_failed(self, task_key, payload, error, attempts):
        conn = self.ensure()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO failed_tasks (task_key, payload, error, attempts) VALUES (%s,%s,%s,%s)",
                (task_key, json.dumps(payload), str(error)[:500], attempts),
            )
        conn.commit()

    def done_names(self, court):
        conn = self.ensure()
        with conn.cursor() as cur:
            cur.execute("SELECT last_name FROM name_summary WHERE court_code=%s", (court,))
            return {r[0] for r in cur.fetchall()}

    def missing_pages(self, court):
        """(name, page, total_records) for every page the site reported but we have not stored."""
        conn = self.ensure()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.last_name, g.page, s.total_records
                FROM name_summary s
                CROSS JOIN LATERAL generate_series(1, s.total_pages) AS g(page)
                LEFT JOIN scraped_pages p
                       ON p.court_code = s.court_code AND p.last_name = s.last_name AND p.page = g.page
                WHERE s.court_code = %s AND s.total_pages > 0 AND p.page IS NULL
                ORDER BY s.last_name, g.page
                """,
                (court,),
            )
            return cur.fetchall()

    def stats(self, court):
        conn = self.ensure()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM cases WHERE court_code=%s", (court,))
            cases = cur.fetchone()[0]
            cur.execute("SELECT count(*), coalesce(sum(total_records),0) FROM name_summary WHERE court_code=%s", (court,))
            names, expected = cur.fetchone()
            cur.execute("SELECT count(*) FROM failed_tasks")
            failed = cur.fetchone()[0]
            cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
            size = cur.fetchone()[0]
        return {"cases": cases, "names_counted": names, "expected_records": int(expected),
                "failed_tasks": failed, "db_size": size}
