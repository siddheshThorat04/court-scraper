"""Create the tables in the MySQL database that DATABASE_URL points to.   python app_schema.py
Safe to run twice. Works even if the database user has no DROP privilege (common on shared hosting)."""
import re

import pymysql

from db import DB

sql = open("schema.sql", encoding="utf-8").read()
sql = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
statements = [s.strip() for s in re.split(r";\s*(?:\n|$)", sql) if s.strip()]

conn = DB().ensure()
with conn.cursor() as cur:
    for s in statements:
        if s.upper().startswith("CREATE OR REPLACE VIEW"):
            # REPLACE needs the DROP privilege. Create the view only if it is missing; never fail on it.
            name = re.search(r"VIEW\s+(\w+)", s, re.I).group(1)
            cur.execute("SELECT COUNT(*) FROM information_schema.views WHERE table_schema = DATABASE() "
                        "AND table_name = %s", (name,))
            if cur.fetchone()[0]:
                continue
            try:
                cur.execute(re.sub(r"CREATE OR REPLACE VIEW", "CREATE VIEW", s, count=1, flags=re.I))
            except pymysql.err.MySQLError as e:
                print(f"note: could not create view {name} ({e.args[-1][:80]}). Optional - scraping does not need it.")
            continue
        cur.execute(s)
conn.commit()
with conn.cursor() as cur:
    cur.execute("SHOW TABLES")
    print("tables now:", sorted(r[0] for r in cur.fetchall()))