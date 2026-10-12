"""Create the tables in the MySQL database that DATABASE_URL points to.   python app_schema.py
Safe to run twice: every statement uses IF NOT EXISTS / OR REPLACE."""
import re

from db import DB

sql = open("schema.sql", encoding="utf-8").read()
sql = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
statements = [s.strip() for s in re.split(r";\s*(?:\n|$)", sql) if s.strip()]

conn = DB().ensure()
with conn.cursor() as cur:
    for s in statements:
        cur.execute(s)
conn.commit()
with conn.cursor() as cur:
    cur.execute("SHOW TABLES")
    print("tables now:", sorted(r[0] for r in cur.fetchall()))
