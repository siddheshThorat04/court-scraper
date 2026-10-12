"""One Chrome per worker. Reads the page with a few JS calls instead of ~9 Selenium lookups per case."""
import logging
import os
import re
import time

import config
from errors import Retry

log = logging.getLogger("browser")

SNAPSHOT_JS = """
const q = s => document.querySelector(s);
const vis = e => e && e.offsetWidth > 0 && e.offsetHeight > 0;
const c = q('#total-count-id');
const inp = q("input[formcontrolname='lastName']");
return {
  captcha: Array.from(document.querySelectorAll("iframe[src*='arkoselabs'],iframe[src*='captcha']")).some(vis),
  tos: vis(q('#continue-button-id')),
  count: c ? c.innerText : null,
  items: document.querySelectorAll('app-case-search-results-item').length,
  nameval: inp ? inp.value : null,
  body: (document.body ? document.body.innerText : '').slice(0, 3000).toLowerCase()
};
"""

EXTRACT_JS = """
const txt = (el, s) => { const n = el.querySelector(s); return n ? n.innerText.trim() : ''; };
return Array.from(document.querySelectorAll('app-case-search-results-item')).map(el => ({
  case_id: txt(el, '.case-id strong'),
  case_title: txt(el, '.entitlement .animate'),
  filed_date: txt(el, '.filed-date .animate'),
  case_status: txt(el, '.case-status .animate'),
  balance: txt(el, '.balance .animate'),
  court: txt(el, '.tenant-description .animate'),
  party_name: txt(el, '.party-name .animate'),
  party_type: txt(el, '.party-type .animate'),
  year_of_birth: txt(el, '.birth-year .animate')
}));
"""

TOS_JS = """
const f = document.querySelector('.card-footer'); if (f) f.scrollIntoView(true);
const b = document.querySelector('#continue-button-id'); if (b) b.click();
"""


def parse_count(snap):
    """'Total Record Count: 108' -> 108 ; None while the number has not rendered."""
    text = snap.get("count")
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def is_empty_state(snap):
    body = snap.get("body") or ""
    return any(t in body for t in config.NO_RESULTS_TEXT)


class Browser:
    def __init__(self, worker_id):
        self.worker_id = worker_id
        self.driver = None
        self.loads = 0

    # ---------- lifecycle ----------
    def start(self):
        from seleniumbase import Driver  # imported lazily so ctl.py/seed.py don't need it
        profile = os.path.abspath(os.path.join(config.PROFILE_DIR, self.worker_id))
        os.makedirs(profile, exist_ok=True)
        log.info("Starting Chrome (profile %s)", profile)
        self.driver = Driver(
            uc=config.USE_UC,
            headless=False,                      # runs on the worker's Xvfb display
            user_data_dir=profile,               # cookies survive restarts -> fewer repeat prompts
            chromium_arg="--disable-dev-shm-usage",
        )
        self.loads = 0

    def close(self):
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:  # noqa: BLE001
                pass
            self.driver = None

    def recycle(self):
        log.info("Recycling browser")
        self.close()
        time.sleep(2)
        self.start()

    def ensure(self):
        if self.driver is None:
            self.start()
        elif self.loads >= config.RECYCLE_AFTER:
            self.recycle()

    def _single_tab(self):
        """Keep exactly one tab open so memory does not creep up."""
        try:
            handles = self.driver.window_handles
            if len(handles) > 1:
                for h in handles[1:]:
                    self.driver.switch_to.window(h)
                    self.driver.close()
                self.driver.switch_to.window(handles[0])
        except Exception as e:  # noqa: BLE001
            log.debug("tab cleanup: %s", e)

    def bring_to_front(self):
        """Raise the window so the operator sees the CAPTCHA straight away."""
        try:
            self.driver.switch_to.window(self.driver.current_window_handle)
            self.driver.maximize_window()
        except Exception as e:  # noqa: BLE001
            log.debug("bring_to_front: %s", e)

    # ---------- page access ----------
    def snapshot(self):
        return self.driver.execute_script(SNAPSHOT_JS)

    def load(self, url, expect_name):
        """Open url and wait until the results (or empty state / CAPTCHA) are really there.

        Returns a snapshot dict. snap['captcha'] True means a CAPTCHA is on screen.
        Raises Retry on timeout.
        """
        self.ensure()
        self._single_tab()
        self.loads += 1
        self.driver.get(url)
        deadline = time.time() + config.PAGE_TIMEOUT
        want = expect_name.strip().upper()
        while time.time() < deadline:
            try:
                snap = self.snapshot()
            except Exception as e:  # noqa: BLE001 - page mid-navigation
                log.debug("snapshot: %s", e)
                time.sleep(0.5)
                continue
            if snap["captcha"]:
                return snap
            if snap["tos"]:
                self.driver.execute_script(TOS_JS)
                time.sleep(1.2)
                continue
            nameval = snap.get("nameval")
            if nameval is None or nameval.strip().upper() == want:
                total = parse_count(snap)
                if total is not None and (snap["items"] > 0 or total == 0):
                    time.sleep(0.6)  # let the list settle
                    snap2 = self.snapshot()
                    if snap2["items"] == snap["items"]:
                        return snap2
                elif total is None and is_empty_state(snap):
                    return snap
            time.sleep(0.5)
        raise Retry(f"timeout waiting for results of {expect_name}")

    def extract(self):
        """All cases on the current page, read in one JS call."""
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.4)
        self.driver.execute_script("window.scrollTo(0, 0);")
        rows = self.driver.execute_script(EXTRACT_JS) or []
        if any(not r["case_id"] for r in rows):  # not fully rendered yet - look once more
            time.sleep(1.5)
            rows = self.driver.execute_script(EXTRACT_JS) or []
        if any(not r["case_id"] for r in rows):
            raise Retry("some cases had no case id (page not fully rendered)")
        return rows
