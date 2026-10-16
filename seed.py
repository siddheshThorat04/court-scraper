#!/usr/bin/env python3
"""Load name prefixes into Redis. Safe to run repeatedly: duplicates are never re-queued.

    python seed.py --file names.xlsx --column NameLead                          # one court (COURT_CODE)
    python seed.py --file names.xlsx --column NameLead --courts court_codes.xlsx   # every name x every court
"""
import argparse
import random
import time

import pandas as pd

import config
import taskqueue as tq
from db import DB


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="names.xlsx")
    ap.add_argument("--column", default="NameLead")
    ap.add_argument("--court", default=config.COURT_CODE)
    ap.add_argument("--courts", default="", help="xlsx/csv with a column of court codes -> seed every name x every court")
    ap.add_argument("--courts-column", default="Code")
    ap.add_argument("--no-shuffle", action="store_true", help="keep name-major order (default: shuffled, which makes the early ETA more accurate)")
    ap.add_argument("--force", action="store_true", help="also queue names already counted in the sheet")
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

    courts = [args.court]
    if args.courts:
        cdf = pd.read_csv(args.courts) if args.courts.lower().endswith(".csv") else pd.read_excel(args.courts)
        if args.courts_column not in cdf.columns:
            raise SystemExit(f"Column '{args.courts_column}' not found in {args.courts}. Columns: {list(cdf.columns)}")
        courts, cseen = [], set()
        for v in cdf[args.courts_column].tolist():
            c = str(v).strip()
            if c and c.lower() != "nan" and c not in cseen:
                cseen.add(c)
                courts.append(c)

    skipped_done = 0
    if not args.force and config.SHEETS_URL and len(courts) == 1:   # sheet pre-check only for the single-court mode
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
    print(f"Building {len(courts) * len(names):,} pairs ({len(names):,} names x {len(courts)} courts)...", flush=True)
    pairs = [(c, n) for c in courts for n in names]
    if not args.no_shuffle:
        random.Random(42).shuffle(pairs)

    try:
        mem = r.info("memory")
        used, cap = int(mem.get("used_memory", 0)), int(mem.get("maxmemory", 0))
        print(f"Redis memory: {used / 1e6:.1f} MB used" + (f" of {cap / 1e6:.0f} MB" if cap else " (no maxmemory set)"))
        if cap and used + len(pairs) * 400 > cap:
            print(f"WARNING: {len(pairs):,} pairs will probably NOT fit (rough guess ~0.4 KB per pair). "
                  f"Use a bigger / local Redis, or seed fewer courts at a time.")
    except Exception:  # noqa: BLE001
        pass

    queued, t0, step = 0, time.time(), 500
    for i in range(0, len(pairs), step):
        q = tq.enqueue_pairs(r, pairs[i:i + step])
        queued += q
        if q:
            r.incrby(tq.C_PAIRS_TOTAL, q)      # per chunk, so progress.py is right even if you press Ctrl+C
        done = min(i + step, len(pairs))
        if (i // step) % 20 == 0 or done == len(pairs):
            rate = done / max(0.001, time.time() - t0)
            print(f"  queued {done:,} / {len(pairs):,} ({done / len(pairs):.0%})  {rate:,.0f} pairs/s  "
                  f"~{(len(pairs) - done) / rate / 60:.1f} min left", flush=True)
    if queued:
        r.delete(tq.COMPLETE)  # allow a new "all done" notification
    r.set(tq.START_TS, time.time(), nx=True)
    print(f"names: {len(seen)} | courts: {len(courts)} | pairs: {len(pairs):,} | already done in sheet: {skipped_done} | "
          f"newly queued: {queued:,} | already queued earlier: {len(pairs) - queued:,}")


if __name__ == "__main__":
    main()
