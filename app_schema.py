"""Create the tables in the database that DATABASE_URL points to.   python apply_schema.py
Safe to run twice: every statement uses IF NOT EXISTS / OR REPLACE."""
import psycopg

import config

with psycopg.connect(config.DATABASE_URL, connect_timeout=30) as conn:
    conn.execute(open("schema.sql", encoding="utf-8").read())
    rows = conn.execute(
        "select table_name from information_schema.tables where table_schema='public' order by 1"
    ).fetchall()
print("tables now:", [r[0] for r in rows])