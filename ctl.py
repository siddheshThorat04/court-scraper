#!/usr/bin/env python3
"""Control & monitoring.   python ctl.py <command>

  status                 queue depth, workers alive, pause state, DB totals
  pause [reason]         pause all workers
  resume                 clear pause (also clears a CAPTCHA pause) and the slow-down multiplier
  stop / clear-stop      graceful stop of all workers / allow starting again
  slow <x>               set delay multiplier (e.g. 2 = twice as slow)
  requeue-dead           put dead-lettered tasks back in the queue
  reconcile [--force]    compare DB with site totals and re-queue any missing pages
"""
import argparse
import json
import time

import config
import taskqueue as tq
from db import DB


def cmd_status(r, _):
    d = tq.depth(r)
    print(f"names : pending={d['name'][0]:>6}  not-yet-delivered={d['name'][1]:>7}")
    print(f"pages : pending={d['page'][0]:>6}  not-yet-delivered={d['page'][1]:>7}")
    print(f"dead  : {r.xlen(tq.S_DEAD)}")
    print(f"saved this run (new rows): {int(r.get(tq.TOTAL) or 0):,}")
    pause = tq.get_pause(r)
    if pause:
        mins = int((time.time() - pause.get('ts', time.time())) / 60)
        print(f"PAUSED : {pause['kind']} by {pause['worker']} for {mins} min - {pause['reason']}")
    else:
        print("paused : no")
    print(f"stop flag: {'SET' if r.exists(tq.STOP) else 'no'} | delay multiplier: x{tq.get_mult(r):.1f}")
    base = r.get(tq.K("cfg:min_interval")) or config.GLOBAL_MIN_INTERVAL
    print(f"shared min interval between page loads: {base}s (x{tq.get_mult(r):.1f} slow-down)")
    try:
        print(f"redis memory: {r.info('memory')['used_memory_human']}")
    except Exception:  # noqa: BLE001
        pass
    print("workers:")
    for key in sorted(r.scan_iter(tq.K("hb:*"))):
        hb = json.loads(r.get(key) or "{}")
        print(f"  {key.split('hb:')[-1]:<8} last seen {int(time.time() - hb.get('ts', 0))}s ago, page loads on this browser: {hb.get('loads')}")
    if config.SHEETS_URL:
        try:
            s = DB().stats(config.COURT_CODE)
            print(f"Sheet: {s['cases']:,} case rows | {s['names_counted']:,} names counted | site-reported total "
                  f"{s['expected_records']:,} | failed tasks {s['failed_tasks']} | size {s['db_size']}")
        except Exception as e:  # noqa: BLE001
            print(f"Sheet: unreachable ({e})")


def cmd_pause(r, a):
    tq.set_pause(r, "manual", "ctl", a.reason or "manual pause")
    print("paused")


def cmd_resume(r, _):
    r.delete(tq.PAUSE, tq.MULT, tq.K("lock:pause_reminder"))
    print("resumed (delay multiplier reset to x1)")


def cmd_stop(r, _):
    r.set(tq.STOP, 1)
    print("stop flag set - workers finish their current task, flush to DB, and exit")


def cmd_clear_stop(r, _):
    r.delete(tq.STOP)
    print("stop flag cleared")


def cmd_slow(r, a):
    r.set(tq.MULT, max(1.0, a.factor))
    print(f"delay multiplier x{max(1.0, a.factor):.1f}")


def cmd_requeue_dead(r, _):
    n = 0
    for mid, data in r.xrange(tq.S_DEAD):
        task = json.loads(data["d"])["task"]
        court, name, page = task["court"], task["name"], task.get("page", 1)
        if task["stage"] == "name":
            r.srem(tq.SEEN_NAME, f"{court}|{name}")
            r.hdel(tq.ATTEMPTS, tq.task_id(task))
            tq.enqueue_names(r, court, [name])
        else:
            r.srem(tq.SEEN_PAGE, f"{court}|{name}|{page}")
            r.hdel(tq.ATTEMPTS, tq.task_id(task))
            tq.enqueue_pages(r, court, name, [page], task["total"])
        r.xdel(tq.S_DEAD, mid)
        n += 1
    r.delete(tq.COMPLETE)
    print(f"re-queued {n} dead tasks")


def cmd_reconcile(r, a):
    if tq.backlog(r) > 0 and not a.force:
        raise SystemExit("Queue is not empty - missing pages may just be in flight. Use --force to override.")
    missing = DB().missing_pages(config.COURT_CODE)
    n = 0
    for name, page, total in missing:
        court = config.COURT_CODE
        r.hdel(tq.ATTEMPTS, f"page|{court}|{name}|{page}", f"name|{court}|{name}|1")
        r.srem(tq.K("done"), f"page|{court}|{name}|{page}", f"name|{court}|{name}")
        if page == 1:
            r.srem(tq.SEEN_NAME, f"{court}|{name}")
            n += tq.enqueue_names(r, court, [name])
        else:
            r.srem(tq.SEEN_PAGE, f"{court}|{name}|{page}")
            n += tq.enqueue_pages(r, court, name, [page], total)
    if n:
        r.delete(tq.COMPLETE)
    print(f"missing pages found: {len(missing)} | re-queued: {n}")


def cmd_rate(r, a):
    r.set(tq.K("cfg:min_interval"), max(0.5, a.seconds))
    print(f"shared minimum interval between page loads (ALL workers combined): {max(0.5, a.seconds)}s")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("pause")
    p.add_argument("reason", nargs="?")
    sub.add_parser("resume")
    sub.add_parser("stop")
    sub.add_parser("clear-stop")
    p = sub.add_parser("slow")
    p.add_argument("factor", type=float)
    sub.add_parser("requeue-dead")
    p = sub.add_parser("rate")
    p.add_argument("seconds", type=float)
    p = sub.add_parser("reconcile")
    p.add_argument("--force", action="store_true")
    a = ap.parse_args()
    r = tq.client()
    {
        "status": cmd_status, "pause": cmd_pause, "resume": cmd_resume, "stop": cmd_stop,
        "clear-stop": cmd_clear_stop, "slow": cmd_slow, "requeue-dead": cmd_requeue_dead,
        "reconcile": cmd_reconcile, "rate": cmd_rate,
    }[a.cmd](r, a)


if __name__ == "__main__":
    main()
