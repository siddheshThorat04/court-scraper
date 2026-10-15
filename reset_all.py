#!/usr/bin/env python3
"""
Interactive cleanup before a fresh run. Run it from the project folder (same venv as the scraper):

    python reset_all.py

It never deletes anything until it has shown you what is there and you confirm.
  1) Redis    - queues, task state, counters, pause flags (shows keys grouped by prefix)
  2) Postgres - lists every table with its row count; you pick which ones to empty

It does NOT touch the Google Sheet - clear that by hand (see the steps I sent).
"""
import os
import sys
from collections import Counter

from dotenv import load_dotenv

load_dotenv()


def ask(msg):
    return input(msg).strip()


def reset_redis():
    import redis

    url = os.getenv("REDIS_URL")
    if not url:
        print("REDIS_URL not set - skipping Redis")
        return
    r = redis.from_url(url, decode_responses=True)
    keys = list(r.scan_iter(count=1000))
    print(f"\n=== Redis: {len(keys)} keys ===")
    if not keys:
        return
    groups = Counter(k.split(":")[0] + (":" + k.split(":")[1] if k.count(":") >= 1 else "") for k in keys)
    for g, n in groups.most_common(25):
        print(f"  {n:>8}  {g}*")
    print("\nIf this Redis is used ONLY by this scraper, delete everything (leave the prefix empty).")
    print("If it is shared with other apps, type the prefix to delete, e.g.  court:")
    prefix = ask("Prefix (empty = ALL keys): ")
    pattern = prefix + "*" if prefix else "*"
    targets = list(r.scan_iter(match=pattern, count=1000))
    print(f"-> {len(targets)} keys match '{pattern}'")
    if not targets:
        return
    if ask("Type DELETE to remove them: ") != "DELETE":
        print("Redis: skipped")
        return
    for i in range(0, len(targets), 500):
        r.unlink(*targets[i:i + 500])
    print(f"Redis: deleted {len(targets)} keys")


def pg_connect(dsn):
    try:
        import psycopg2
        return psycopg2.connect(dsn)
    except ImportError:
        import psycopg
        return psycopg.connect(dsn)


def reset_postgres():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set - skipping Postgres")
        return
    conn = pg_connect(dsn)
    cur = conn.cursor()
    cur.execute("""SELECT table_name FROM information_schema.tables
                   WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1""")
    tables = [t[0] for t in cur.fetchall()]
    print(f"\n=== Postgres: {len(tables)} tables ===")
    counts = {}
    for i, t in enumerate(tables, 1):
        cur.execute(f'SELECT count(*) FROM public."{t}"')
        counts[t] = cur.fetchone()[0]
        print(f"  {i:>2}. {t:<35} {counts[t]:>10} rows")
    print("\nPick the SCRAPED-DATA tables to empty (cases, names, tasks, etc.).")
    print("Do NOT pick login/operator/config tables if you want to keep them.")
    sel = ask("Numbers separated by commas (e.g. 1,2,5), 'all', or empty to skip: ")
    if not sel:
        print("Postgres: skipped")
        return
    chosen = tables if sel.lower() == "all" else [tables[int(x) - 1] for x in sel.split(",") if x.strip()]
    print("Will empty:", ", ".join(chosen))
    if ask("Type RESET to confirm: ") != "RESET":
        print("Postgres: skipped")
        return
    names = ", ".join(f'public."{t}"' for t in chosen)
    cur.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    conn.commit()
    print("Postgres: emptied", len(chosen), "tables")


def main():
    print("Make sure all workers (and central_notifier.py) are STOPPED before continuing.")
    if ask("Workers stopped? (y/n): ").lower() != "y":
        sys.exit("Stop them first (Ctrl+C in each window), then rerun.")
    reset_redis()
    reset_postgres()
    print("\nDone. Now clear the Google Sheet by hand, then seed your new names.")


if __name__ == "__main__":
    main()