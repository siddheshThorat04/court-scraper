#!/usr/bin/env python3
"""Load name prefixes into Redis. Safe to run repeatedly: duplicates are never re-queued.

    python seed.py --file names.xlsx --column NameLead
"""
import argparse

import pandas as pd

import config
import taskqueue as tq
from db import DB


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="names.xlsx")
    ap.add_argument("--column", default="NameLead")
    ap.add_argument("--court", default=config.COURT_CODE)
    ap.add_argument("--force", action="store_true", help="also queue names already counted in the DB")
    ap.add_argument("--new-config", action="store_true", help="accept changed dates/filters (only for a brand-new job)")
    args = ap.parse_args()

    df = pd.read_csv(args.file) if args.file.lower().endswith(".csv") else pd.read_excel(args.file)
    if args.column not in df.columns:
        raise SystemExit(f"Column '{args.column}' not found. Columns: {list(df.columns)}")

    names = []
    seen = set()
    for v in df[args.column].tolist():
        s = str(v).strip()
        if not s or s.lower() == "nan" or s in seen:
            continue
        seen.add(s)
        names.append(s)

    skipped_done = 0
    if not args.force and config.DATABASE_URL:
        done = DB().done_names(args.court)
        before = len(names)
        names = [n for n in names if n not in done]
        skipped_done = before - len(names)

    r = tq.client()
    tq.ensure_groups(r)
    fp, fpkey = config.fingerprint(), tq.K("cfg:fingerprint")
    cur = r.get(fpkey)
    if cur and cur != fp and not args.new_config:
        raise SystemExit("Search settings (dates/filters) differ from what this queue was seeded with - mixing "
                         "them gives inconsistent data. Use --new-config only when starting a new job.")
    r.set(fpkey, fp)
    queued = 0
    for i in range(0, len(names), 500):
        queued += tq.enqueue_names(r, args.court, names[i:i + 500])
    r.set(tq.START_TS, __import__("time").time(), nx=True)
    if queued:
        r.delete(tq.COMPLETE)  # allow a new "all done" notification
    print(f"unique names in file: {len(seen)} | already done in DB: {skipped_done} | "
          f"newly queued: {queued} | already queued earlier: {len(names) - queued}")


if __name__ == "__main__":
    main()
