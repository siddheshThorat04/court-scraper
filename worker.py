#!/usr/bin/env python3
"""One scraper worker. Run several of them (see run_workers.sh).

Stage 1 ("name" task): open page 1 of a name prefix -> read total count, save page-1 cases,
                       enqueue pages 2..N.   (saves one request per name vs. a separate counter)
Stage 2 ("page" task): open page N -> save its cases.

CAPTCHA -> pause ALL workers, WhatsApp alert, wait for you to solve it over VNC, then auto-resume.
Exit codes: 0 = clean stop, 1 = crash (run_workers.sh restarts it).
"""
import json
import logging
import math
import random
import signal
import sys
import time
import traceback

import requests

import config
import taskqueue as tq
from browser import Browser, is_empty_state, parse_count
from db import DB
from errors import Retry, Stop
from notifier import Notifier

log = logging.getLogger("worker")


class Buffer:
    def __init__(self):
        self.clear()

    def clear(self):
        self.cases, self.pages, self.summaries = [], [], []
        self.acks = {}          # stream -> [msg ids]
        self.tids = []          # task ids, to clear their attempt counters after commit
        self.since = None

    def empty(self):
        return not (self.cases or self.pages or self.summaries or self.acks)

    def touch(self):
        if self.since is None:
            self.since = time.time()

    def add_ack(self, stream, mid, tid=None):
        self.acks.setdefault(stream, []).append(mid)
        if tid:
            self.tids.append(tid)
        self.touch()

    def due(self):
        if self.empty():
            return False
        return len(self.cases) >= config.BATCH_SIZE or (time.time() - (self.since or time.time())) >= config.FLUSH_SECONDS


class Worker:
    def __init__(self):
        self.id = config.WORKER_ID
        self.r = tq.client()
        self.db = DB()
        self.n = Notifier(self.id, self.r)
        self.browser = Browser(self.id)
        self.buf = Buffer()
        self.consec_fail = 0
        self.stop_requested = False
        self.db_alerted = False
        self.last_tick = 0.0
        self.started = time.time()
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

    def _on_signal(self, *_):
        log.info("Stop signal received - finishing current task")
        self.stop_requested = True

    def should_stop(self):
        return self.stop_requested or bool(self.r.exists(tq.STOP))

    # ------------------------------------------------------------------ housekeeping
    def sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self.should_stop():
                return
            self.tick()
            time.sleep(min(0.5, max(0, end - time.time())))

    def tick(self):
        now = time.time()
        if now - self.last_tick < 5:
            return
        self.last_tick = now
        try:
            self.r.set(tq.K(f"hb:{self.id}"), json.dumps({"ts": now, "loads": self.browser.loads}), ex=120)
            self.n.tick()
            if self.r.set(tq.K("lock:monitor"), self.id, nx=True, ex=60):
                self.monitor()
            if config.HEALTHCHECK_URL and self.r.set(tq.K("lock:hcping"), 1, nx=True, ex=300):
                try:
                    requests.get(config.HEALTHCHECK_URL, timeout=10)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001 - housekeeping must never kill a worker
            log.warning("tick error: %s", e)

    def monitor(self):
        """Runs on one worker at a time (Redis lock)."""
        pause = tq.get_pause(self.r)
        if pause:
            mins = int((time.time() - pause.get("ts", time.time())) / 60)
            if self.r.set(tq.K("lock:pause_reminder"), 1, nx=True, ex=config.PAUSE_REMINDER_SECONDS):
                kind = "captcha" if pause.get("kind") == "captcha" else "stall"
                self.n.critical(kind, f"Still PAUSED ({pause.get('kind')}) for {mins} min. {pause.get('reason')}\n"
                                      f"Worker: {pause.get('worker')}  |  after fixing: python ctl.py resume")
            return
        backlog = tq.backlog(self.r)
        last = float(self.r.get(tq.LAST_PROGRESS) or self.started)
        if backlog > 0 and time.time() - max(last, self.started) > config.STALL_MINUTES * 60:
            self.n.critical("stall", f"No progress for {config.STALL_MINUTES}+ min with {backlog} tasks queued. "
                                     f"Check workers: python ctl.py status", key="stall", throttle=1800)
        total = int(self.r.get(tq.TOTAL) or 0)
        if backlog == 0 and total > 0 and self.buf.empty() and self.r.set(tq.COMPLETE, 1, nx=True):
            self.n.info("complete", f"✅ ALL DONE. {total:,} records saved. Run: python ctl.py reconcile")

    # ------------------------------------------------------------------ pause / captcha
    def rate_gate(self):
        """Global spacing between page loads across all workers."""
        base = float(self.r.get(tq.K("cfg:min_interval")) or config.GLOBAL_MIN_INTERVAL)   # owner-controlled, shared
        px = int(base * tq.get_mult(self.r) * 1000)
        deadline = time.time() + 120
        while not self.r.set(tq.GATE, 1, nx=True, px=px):
            if self.should_stop() or time.time() > deadline:
                return
            time.sleep(0.25)

    @staticmethod
    def local_alert(msg):
        bar = "=" * 64
        print(f"\n{bar}\n  ACTION NEEDED: {msg}\n{bar}\n", flush=True)
        try:
            import winsound
            for _ in range(3):
                winsound.Beep(1000, 250)
        except Exception:  # noqa: BLE001 - not Windows / no sound device
            print("\a", end="", flush=True)

    def handle_captcha(self, name, page):
        """A CAPTCHA needs a human. 'local': only THIS worker waits (the operator is at this machine);
        'global': every worker pauses (single VM / remote-desktop setups)."""
        local = config.CAPTCHA_MODE == "local"
        if local:
            self.local_alert(f"A CAPTCHA appeared ({name}, page {page}). Solve it in the Chrome window - "
                             f"scraping continues by itself afterwards.")
            alert = (f"CAPTCHA on {self.id} ({name} p{page}). The operator at that machine is solving it; "
                     f"other workers keep going.")
        else:
            tq.set_pause(self.r, "captcha", self.id, f"CAPTCHA while loading {name} p{page}")
            self.r.set(tq.K("lock:pause_reminder"), 1, nx=True, ex=config.PAUSE_REMINDER_SECONDS)
            hint = config.CAPTCHA_HINT or (f"VNC port: {config.VNC_PORT} (via your SSH tunnel)" if config.VNC_PORT
                                           else "Open that worker's browser window")
            alert = (f"CAPTCHA on worker {self.id} ({name} page {page}). ALL workers paused.\n{hint}\n"
                     f"Solve it; scraping resumes automatically.")
        self.n.critical("captcha", alert, key=f"captcha:{self.id}", throttle=120)
        self.browser.bring_to_front()
        started = last_beep = last_remind = time.time()
        while True:
            if self.should_stop():
                raise Stop()
            self.tick()
            time.sleep(4)
            try:
                if not self.browser.snapshot()["captcha"]:
                    break
            except Exception:  # noqa: BLE001
                pass
            now = time.time()
            if local and now - last_beep > 20:
                self.local_alert("Still waiting for the CAPTCHA to be solved.")
                last_beep = now
            if now - last_remind > config.PAUSE_REMINDER_SECONDS:
                self.n.critical("captcha", f"{self.id} has been waiting {int((now - started) / 60)} min on a CAPTCHA.")
                last_remind = now
        # Everyone slows down a little after each CAPTCHA; the slow-down fades after an hour without one.
        mult = min(config.MAX_MULT, tq.get_mult(self.r) * 1.5)
        self.r.set(tq.MULT, mult, ex=3600)
        self.n.info("captcha", f"CAPTCHA solved on {self.id}. Cooling down {config.CAPTCHA_COOLDOWN}s "
                               f"(shared delay now x{mult:.1f}).")
        self.sleep(config.CAPTCHA_COOLDOWN)
        if not local:
            tq.clear_pause(self.r, owner=self.id)

    def load_page(self, task, page):
        url = config.build_url(task["court"], task["name"], page)
        while True:
            snap = self.browser.load(url, task["name"])
            if snap.get("captcha"):
                log.info("CAPTCHA frames seen: %s", snap.get("capinfo"))
                self.handle_captcha(task["name"], page)
                # The solved CAPTCHA usually lets the CURRENT page continue. Re-opening the URL throws that
                # away and often triggers a brand-new CAPTCHA, so first wait for results on this same page.
                snap = self.browser.wait_ready(task["name"], 20, soft=True)
                if snap is not None and not snap.get("captcha"):
                    return snap
                continue
            return snap

    # ------------------------------------------------------------------ task handlers
    @staticmethod
    def _rows(task, page, cases):
        return [
            (task["court"], c["case_id"], c["party_name"], c["party_type"], c["case_title"], c["filed_date"],
             c["case_status"], c["balance"], c["court"], c["year_of_birth"], task["name"], page)
            for c in cases
        ]

    def do_name(self, stream, mid, task, attempts):
        name, court = task["name"], task["court"]
        snap = self.load_page(task, 1)
        total = parse_count(snap)
        if total is None:
            if is_empty_state(snap):
                total = 0
            else:
                raise Retry("no count element and no empty-state text")
        if total == 0:
            self.buf.summaries.append((court, name, 0, 0, "empty"))
            self.buf.add_ack(stream, mid, tq.task_id(task))
            return
        pages = math.ceil(total / config.PAGE_SIZE)
        cases = self.browser.extract()
        expected = min(config.PAGE_SIZE, total)
        if len(cases) < expected and attempts < config.MAX_ATTEMPTS:
            raise Retry(f"page 1 had {len(cases)} of {expected} cases")
        if pages > config.WARN_PAGES:
            self.n.warn(f"{name} has {pages} pages ({total} records) - site may cap results; check completeness")
        tq.enqueue_pages(self.r, court, name, range(2, pages + 1), total)
        self.buf.summaries.append((court, name, total, pages, "counted"))
        self.buf.pages.append((court, name, 1, len(cases)))
        self.buf.cases.extend(self._rows(task, 1, cases))
        self.buf.add_ack(stream, mid, tq.task_id(task))

    def do_page(self, stream, mid, task, attempts):
        page, total = task["page"], task["total"]
        self.load_page(task, page)
        cases = self.browser.extract()
        expected = min(config.PAGE_SIZE, max(0, total - (page - 1) * config.PAGE_SIZE))
        if len(cases) < expected and attempts < config.MAX_ATTEMPTS:
            raise Retry(f"page {page} had {len(cases)} of {expected} cases")
        self.buf.pages.append((task["court"], task["name"], page, len(cases)))
        self.buf.cases.extend(self._rows(task, page, cases))
        self.buf.add_ack(stream, mid, tq.task_id(task))

    # ------------------------------------------------------------------ failure handling
    def dead_letter(self, stream, mid, task, error, attempts):
        tid = tq.task_id(task)
        tq.dead_letter(self.r, stream, mid, task, error)
        try:
            self.db.record_failed(tid, task, error, attempts)
        except Exception as e:  # noqa: BLE001
            log.error("could not record failed task in DB: %s", e)
        self.n.error("error", f"Task gave up after {attempts} attempts: {tid}\n{str(error)[:200]}", key="deadletter")

    def on_failure(self, stream, mid, task, attempts, err):
        self.consec_fail += 1
        tid = tq.task_id(task)
        log.warning("task %s failed (attempt %d): %s", tid, attempts, err)
        if attempts >= config.MAX_ATTEMPTS:
            self.dead_letter(stream, mid, task, err, attempts)
        else:
            self.n.warn(f"{tid} attempt {attempts}/{config.MAX_ATTEMPTS}: {str(err)[:120]}")
        if self.consec_fail % 3 == 0:
            try:
                self.browser.recycle()
            except Exception as e:  # noqa: BLE001
                log.error("browser recycle failed: %s", e)
        if self.consec_fail >= config.CONSEC_FAIL_LIMIT:
            tq.set_pause(self.r, "failures", self.id, f"{self.consec_fail} consecutive failures: {str(err)[:150]}")
            self.n.critical("error", f"{self.consec_fail} failures in a row on {self.id} - ALL workers paused.\n"
                                     f"Last error: {str(err)[:200]}\nInvestigate, then: python ctl.py resume")
            self.consec_fail = 0

    # ------------------------------------------------------------------ DB flush
    def flush(self):
        b = self.buf
        if b.empty():
            return True
        inserted = None
        for attempt in range(3):
            try:
                inserted = self.db.write(b.cases, b.pages, b.summaries)
                break
            except Exception as e:  # noqa: BLE001
                log.error("DB write failed (try %d): %s", attempt + 1, e)
                self.db.reset()
                time.sleep(3 * (attempt + 1))
        if inserted is None:
            if not self.db_alerted:
                self.db_alerted = True
                self.n.critical("db", f"DB write failing on {self.id}. {len(b.cases)} rows held in memory; "
                                      f"worker will keep retrying.", key="db", throttle=600)
            return False
        if self.db_alerted:
            self.db_alerted = False
            self.n.info("db", f"DB writes recovered on {self.id}.")
        for stream, ids in b.acks.items():
            tq.ack(self.r, stream, ids)
        if b.tids:
            self.r.hdel(tq.ATTEMPTS, *b.tids)   # keep Redis small
        total = self.r.incrby(tq.TOTAL, inserted)
        self.r.set(tq.LAST_PROGRESS, time.time())
        log.info("flushed: %d new rows (scraped %d, %d pages) total=%d", inserted, len(b.cases), len(b.pages), total)
        self.check_milestone(total, inserted)
        b.clear()
        return True

    def check_milestone(self, total, inserted):
        step = config.PROGRESS_EVERY
        if step <= 0 or inserted <= 0:
            return
        hi, lo = total // step, (total - inserted) // step
        if hi > lo and self.r.set(tq.K(f"notified:ms:{hi}"), 1, nx=True):
            elapsed = max(1.0, time.time() - float(self.r.get(tq.START_TS) or self.started))
            per_hour = int(total / elapsed * 3600)
            d = tq.depth(self.r)
            queued_pages = sum(d["page"])
            self.n.info("progress", f"{hi * step:,} records saved\n"
                                    f"Rate ~{per_hour:,}/hour | queued pages: {queued_pages:,} "
                                    f"(~{queued_pages * config.PAGE_SIZE:,} records) | names waiting: {sum(d['name']):,}")

    # ------------------------------------------------------------------ main loop
    def run(self):
        config.validate()
        tq.ensure_groups(self.r)
        shared = self.r.get(tq.K("cfg:fingerprint"))
        if shared and shared != config.fingerprint():
            raise SystemExit("Your search settings (dates / filters) differ from the shared job settings.\n"
                             "Use the .env you were given and do not edit the dates or filters.")
        self.r.set(tq.START_TS, time.time(), nx=True)
        self.n.info("start", f"Worker {self.id} started (batch {config.BATCH_SIZE}, delay {config.MIN_DELAY}-{config.MAX_DELAY}s)")
        try:
            while True:
                if self.should_stop():
                    break
                self.tick()
                if tq.get_pause(self.r):
                    if not self.buf.empty():
                        self.flush()
                    time.sleep(5)
                    continue
                if self.buf.due():
                    if not self.flush():
                        time.sleep(10)
                        continue
                item = tq.fetch(self.r, self.id)
                if item is None:
                    if not self.buf.empty():
                        self.flush()
                    else:
                        time.sleep(5)
                    continue
                self.handle(item)
                self.sleep(random.uniform(config.MIN_DELAY, config.MAX_DELAY) * tq.get_mult(self.r))
        except Stop:
            pass
        except Exception as e:  # noqa: BLE001
            tail = traceback.format_exc()[-600:]
            self.n.critical("crash", f"Worker {self.id} CRASHED: {e}\n{tail}")
            self._shutdown()
            raise
        self._shutdown()

    def handle(self, item):
        stream, mid, task = item
        tid = tq.task_id(task)
        attempts = self.r.hincrby(tq.ATTEMPTS, tid, 1)
        if attempts > config.MAX_ATTEMPTS:
            self.dead_letter(stream, mid, task, "max attempts exceeded (worker died or kept failing)", attempts - 1)
            return
        try:
            self.rate_gate()
            if task["stage"] == "name":
                self.do_name(stream, mid, task, attempts)
            else:
                self.do_page(stream, mid, task, attempts)
            self.consec_fail = 0
        except Stop:
            raise
        except Exception as e:  # noqa: BLE001 - includes Retry
            self.on_failure(stream, mid, task, attempts, e)

    def _shutdown(self):
        try:
            self.flush()
        except Exception as e:  # noqa: BLE001
            log.error("final flush failed: %s", e)
        self.browser.close()
        self.n.info("stop", f"Worker {self.id} stopped.")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s %(levelname)s [{config.WORKER_ID}] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    try:
        Worker().run()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        log.exception("fatal")
        sys.exit(1)


if __name__ == "__main__":
    main()
