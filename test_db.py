"""Checks the MySQL connection and tells you what is wrong in plain words.   python test_db.py
Never prints the password."""
import socket

import config
from db import DB, parse_url

kw = parse_url(config.DATABASE_URL)
print(f"host={kw['host']} port={kw['port']} user={kw['user']} database={kw['database']} ssl={'ssl' in kw}")

try:
    socket.create_connection((kw["host"], kw["port"]), timeout=10).close()
    print("1. network to host:port ........ OK")
except OSError as e:
    raise SystemExit(f"1. network to host:port ........ FAILED ({e})\n"
                     "   -> wrong host/port, or your IP is not allowed under cPanel > Remote MySQL.")
try:
    c = DB().ensure()
    with c.cursor() as cur:
        cur.execute("SELECT VERSION(), DATABASE(), CURRENT_USER()")
        print("2. login ....................... OK ->", cur.fetchone())
        cur.execute("SHOW TABLES")
        print("3. tables ...................... ", sorted(r[0] for r in cur.fetchall()) or "none yet (run: python app_schema.py)")
except Exception as e:  # noqa: BLE001
    raise SystemExit(f"2. login ....................... FAILED ({str(e)[:200]})\n"
                     "   -> check user/password, or try adding ?ssl=insecure to DATABASE_URL.")
