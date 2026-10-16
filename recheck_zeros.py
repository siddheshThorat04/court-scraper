#!/usr/bin/env python3
"""Re-queue (court, name) pairs that were saved as EMPTY (0 records) - e.g. because the site answered HTTP 429 while
the page still showed 'Total Record Count: 0'.

  python recheck_zeros.py                                   # DRY RUN: hour-by-hour table of how many pairs came back empty
  python recheck_zeros.py --since "2026-10-16 09:00" --until "2026-10-16 15:00"      # dry run for that window
  python recheck_zeros.py --since "2026-10-16 09:00" --until "2026-10-16 15:00" --apply

Times without a zone are read as THIS computer's local time. Nothing changes without --apply.
Stop the workers first. Re-checked pairs go through the normal census again (one page load each); the new result is
appended to the 'names' sheet (the latest row wins) and the progress counters are corrected.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone

import taskqueue as tq


def parse_ts(v):
    s = str(v).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.astimezone()          # naive -> local time
    return d.astimezone(timezone.utc)


def as_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return -1


def main(argv=None, r=None, db=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    r = r or tq.client()
    if db is None:
        from db import DB
        db = DB()
    since, until = (parse_ts(a.since) if a.since else None), (parse_ts(a.until) if a.until else None)

    latest = {}
    for row in db._read("names", 6):          # later rows win, same rule the sheet code uses
        latest[(str(row[0]), str(row[1]))] = row
    print(f"{len(latest):,} (court, name) pairs have a result in the sheet")

    per_hour = defaultdict(lambda: [0, 0])    # local hour -> [all pairs, empty pairs]
    cands = []
    for (court, name), row in latest.items():
        ts = parse_ts(row[5]) if len(row) > 5 else None
        empty = as_int(row[2]) == 0 and str(row[4]).lower() == "empty"
        if ts:
            h = ts.astimezone().strftime("%Y-%m-%d %H:00")
            per_hour[h][0] += 1
            per_hour[h][1] += empty
        if empty and ts and (not since or ts >= since) and (not until or ts <= until):
            cands.append((court, name))

    print("\nhour (local)        pairs  empty  empty%")
    for h in sorted(per_hour)[-72:]:
        n, z = per_hour[h]
        print(f"{h}   {n:6d} {z:6d}  {z / n:6.0%}" + ("   <-- unusually high?" if n >= 20 and z / n > 0.9 else ""))
    print(f"\nEmpty pairs inside the chosen window: {len(cands):,}")
    if not a.apply:
        print("Dry run - nothing changed. Add --apply (with --since/--until) to re-queue them.")
        return 0
    if not cands:
        return 0

    done_key, requeued = tq.K("done"), 0
    for i in range(0, len(cands), 500):
        chunk = cands[i:i + 500]
        pipe = r.pipeline()
        for court, name in chunk:
            m = f"{court}|{name}"
            pipe.srem(tq.SEEN_NAME, m)
            pipe.srem(tq.C_SEEN, m)
            pipe.srem(done_key, f"name|{court}|{name}")      # lets the new summary row be written again
        res = pipe.execute()
        was_counted = sum(res[1::3])
        if was_counted:                                       # undo the census counts of these pairs
            r.decrby(tq.C_PAIRS_DONE, was_counted)
            r.decrby(tq.C_PAIRS_ZERO, was_counted)
        requeued += tq.enqueue_pairs(r, chunk)
    r.delete(tq.COMPLETE)
    print(f"Re-queued {requeued:,} pairs. Start the workers; progress.py will show them as not yet counted again.")
    return requeued


if __name__ == "__main__":
    main()
