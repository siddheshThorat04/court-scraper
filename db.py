"""MySQL / MariaDB access (PyMySQL). One transaction per flush; every insert is idempotent."""
import json
import logging
import ssl as _ssl
from urllib.parse import parse_qs, unquote, urlparse

import pymysql

import config

log = logging.getLogger("db")

# Inserting a duplicate case key is a harmless no-op (and, unlike INSERT IGNORE, other errors still surface).
SQL_CASE = """
INSERT INTO cases (court_code, case_id, party_name, party_type, case_title, filed_date,
                   case_status, balance, court, year_of_birth, search_last_name, page_number)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE case_id = case_id
"""

SQL_PAGE = """
INSERT INTO scraped_pages (court_code, last_name, page, case_count) VALUES (%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE case_count = VALUES(case_count), scraped_at = CURRENT_TIMESTAMP
"""

SQL_SUMMARY = """
INSERT INTO name_summary (court_code, last_name, total_records, total_pages, status)
VALUES (%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE total_records = VALUES(total_records), total_pages = VALUES(total_pages),
                        status = VALUES(status), counted_at = CURRENT_TIMESTAMP
"""

# column widths in schema.sql: (index in the case tuple, max chars). Clipped so one odd value can never
# make a whole batch fail forever.
_CLIP = {1: 64, 2: 255, 3: 64, 5: 64, 6: 128, 7: 64, 8: 255, 9: 16, 10: 100}


def parse_url(url):
    """mysql://user:password@host:3306/dbname?ssl=true   (percent-encode special characters in the password)"""
    u = urlparse(url)
    if u.scheme not in ("mysql", "mysql+pymysql", "mariadb"):
        raise SystemExit("DATABASE_URL must look like mysql://USER:PASSWORD@HOST:3306/DATABASE")
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    kw = dict(host=u.hostname, port=u.port or 3306, user=unquote(u.username or ""),
              password=unquote(u.password or ""), database=u.path.lstrip("/"))
    mode = q.get("ssl", "").lower()
    if mode in ("1", "true", "yes"):
        kw["ssl"] = _ssl.create_default_context()
    elif mode == "insecure":                      # encrypted but certificate not verified (self-signed hosts)
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        kw["ssl"] = ctx
    return kw


def _clip_case(row):
    row = list(row)
    for i, n in _CLIP.items():
        if isinstance(row[i], str) and len(row[i]) > n:
            row[i] = row[i][:n]
    return tuple(row)


class DB:
    def __init__(self, url=None):
        self.url = url or config.DATABASE_URL
        self.conn = None

    def _connect(self):
        self.conn = pymysql.connect(charset="utf8mb4", autocommit=False, connect_timeout=20,
                                    read_timeout=60, write_timeout=60, **parse_url(self.url))

    def ensure(self):
        if self.conn is None:
            self._connect()
        else:
            try:
                self.conn.ping(reconnect=True)     # shared hosts drop idle connections quickly
            except Exception:
                self.reset()
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
                    cur.executemany(SQL_CASE, [_clip_case(c) for c in cases])
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
                (task_key[:400], json.dumps(payload), str(error)[:500], attempts),
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
        out = []
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.last_name, s.total_pages, s.total_records
                FROM name_summary s
                LEFT JOIN scraped_pages p ON p.court_code = s.court_code AND p.last_name = s.last_name
                WHERE s.court_code = %s AND s.total_pages > 0
                GROUP BY s.last_name, s.total_pages, s.total_records
                HAVING COUNT(p.page) < s.total_pages
                ORDER BY s.last_name
                """,
                (court,),
            )
            incomplete = cur.fetchall()
            for name, total_pages, total_records in incomplete:
                cur.execute("SELECT page FROM scraped_pages WHERE court_code=%s AND last_name=%s", (court, name))
                have = {r[0] for r in cur.fetchall()}
                out += [(name, p, total_records) for p in range(1, total_pages + 1) if p not in have]
        return out

    def stats(self, court):
        conn = self.ensure()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM cases WHERE court_code=%s", (court,))
            cases = cur.fetchone()[0]
            cur.execute("SELECT count(*), coalesce(sum(total_records),0) FROM name_summary WHERE court_code=%s", (court,))
            names, expected = cur.fetchone()
            cur.execute("SELECT count(*) FROM failed_tasks")
            failed = cur.fetchone()[0]
            cur.execute("SELECT coalesce(round(sum(data_length+index_length)/1048576,1),0) "
                        "FROM information_schema.tables WHERE table_schema = DATABASE()")
            size = f"{cur.fetchone()[0]} MB"
        conn.commit()
        return {"cases": cases, "names_counted": names, "expected_records": int(expected),
                "failed_tasks": failed, "db_size": size}
