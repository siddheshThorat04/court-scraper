"""Tries a few variants of DATABASE_URL and prints which connect. Never prints the password.
    python test_db.py
"""
import psycopg

import config

url = config.DATABASE_URL
variants = {
    "as is": url,
    "without channel_binding": url.replace("&channel_binding=require", ""),
    "direct host (no -pooler)": url.replace("-pooler", ""),
    "direct, no channel_binding": url.replace("-pooler", "").replace("&channel_binding=require", ""),
}
for label, u in variants.items():
    try:
        psycopg.connect(u, connect_timeout=20).close()
        print(f"{label:28} -> OK")
    except Exception as e:  # noqa: BLE001
        print(f"{label:28} -> {str(e).splitlines()[0][:100]}")