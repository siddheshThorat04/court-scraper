#!/usr/bin/env python3
"""Census-first progress + ETA.   python progress.py            (once)
                                 python progress.py --watch 30  (refresh every 30 s)

Phase 1 (census): every (court, name) pair loads page 1 -> its total record count and page count become known.
Phase 2: the remaining pages 2..N are scraped.
Until the census is finished, the grand totals are ESTIMATES (pairs counted so far, scaled up to all pairs);
afterwards they are exact.
"""
import argparse
import time

import taskqueue as tq


def bar(p, width=28):
    p = max(0.0, min(1.0, p))
    n = int(p * width)
    return "[" + "#" * n + "-" * (width - n) + f"] {p * 100:5.1f}%"


def hms(sec):
    if sec is None:
        return "n/a"
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return (f"{d}d " if d else "") + f"{h}h {m:02d}m"


def recent_rate(r, window=1800):
    """Finished tasks per second over the last `window` seconds (needs >= 60 s of samples)."""
    now = time.time()
    rows = r.zrangebyscore(tq.P_SAMPLES, now - window, now, withscores=True)
    if len(rows) < 2:
        return None
    (m0, t0), (m1, t1) = rows[0], rows[-1]
    d0, d1 = int(m0.split(":")[1]), int(m1.split(":")[1])
    if t1 - t0 < 60 or d1 <= d0:
        return None
    return (d1 - d0) / (t1 - t0)


def show(r):
    g = lambda k: int(r.get(k) or 0)
    pairs_total, pairs_done, zero = g(tq.C_PAIRS_TOTAL), g(tq.C_PAIRS_DONE), g(tq.C_PAIRS_ZERO)
    rec_known, pages_known = g(tq.C_RECORDS), g(tq.C_PAGES)
    loads_done, pages_done, saved = g(tq.P_LOADS), g(tq.P_PAGES), g(tq.TOTAL)
    if not pairs_total:
        print("Nothing seeded yet (run seed.py first).")
        return

    nonzero = pairs_done - zero
    extra_known = max(0, pages_known - nonzero)            # pages 2..N of non-empty pairs, known so far
    complete = pairs_done >= pairs_total
    scale = 1.0 if complete else (pairs_total / pairs_done if pairs_done else 0.0)
    est_loads = pairs_total + extra_known * scale if pairs_done else 0
    est_records = rec_known * scale
    est_pages = pages_known * scale
    est = "" if complete else "  (estimate)"

    print(f"CENSUS   {bar(pairs_done / pairs_total)}  {pairs_done:,} / {pairs_total:,} pairs counted"
          f"  ({zero:,} empty, {nonzero:,} with records)")
    if pairs_done:
        print(f"TOTALS   records {est_records:,.0f}   pages {est_pages:,.0f}   page loads incl. census {est_loads:,.0f}{est}")
        print(f"OVERALL  {bar(loads_done / est_loads if est_loads else 0)}  {loads_done:,} / {est_loads:,.0f} page loads{est}")
        print(f"RECORDS  {bar(saved / est_records if est_records else 0)}  {saved:,} / {est_records:,.0f} saved{est}")
    rate = recent_rate(r)
    start = float(r.get(tq.START_TS) or time.time())
    avg = loads_done / max(1.0, time.time() - start)
    use = rate or (avg if loads_done else None)
    if use and est_loads:
        print(f"SPEED    {use * 60:,.1f} page loads/min ({'last 30 min' if rate else 'average since start'})"
              f"   ETA {hms((est_loads - loads_done) / use)}{'' if complete else '  (rough until census is done)'}")
    else:
        print("SPEED    waiting for enough data...")

    caps = {k: int(v) for k, v in r.hgetall(tq.CAPTCHA_COUNT).items()}
    if caps:
        tot = sum(caps.values())
        per = f"{loads_done / tot:,.0f} page loads per CAPTCHA" if loads_done else ""
        print(f"CAPTCHA  {tot:,} total ({per}) | " + ", ".join(f"{m}: {n}" for m, n in sorted(caps.items())))

    d = tq.depth(r)
    pause = tq.get_pause(r)
    print(f"QUEUE    count tasks: {sum(d['name']):,} waiting/in-flight | page tasks: {sum(d['page']):,} | "
          f"dead: {r.xlen(tq.S_DEAD):,} | delay x{tq.get_mult(r):.1f}"
          + (f" | PAUSED ({pause.get('kind')})" if pause else ""))
    try:
        m = r.info("memory")
        cap = int(m.get("maxmemory", 0))
        print(f"REDIS    {int(m['used_memory']) / 1e6:.1f} MB used" + (f" of {cap / 1e6:.0f} MB" if cap else ""))
    except Exception:  # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="refresh every N seconds")
    a = ap.parse_args()
    r = tq.client()
    while True:
        print(time.strftime("\n== %Y-%m-%d %H:%M:%S =="))
        show(r)
        if not a.watch:
            break
        time.sleep(a.watch)


if __name__ == "__main__":
    main()
