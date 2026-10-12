"""Checks the Google Sheets web app connection.   python test_db.py   (never prints the secret)"""
import config
from db import DB

print("SHEETS_URL:", (config.SHEETS_URL[:60] + "...") if config.SHEETS_URL else "NOT SET")
try:
    s = DB()._call("stats")
    print(f"OK - cases rows: {s['cases']}, failed rows: {s['failed']}, cells used: {s['cells']:,} of 10,000,000")
except Exception as e:  # noqa: BLE001
    raise SystemExit(f"FAILED: {str(e)[:250]}\n"
                     "-> unauthorized: SHEETS_SECRET differs from the SECRET script property.\n"
                     "-> 'did not return JSON': redeploy the web app with access set to 'Anyone'.")
