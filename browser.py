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
// A *challenge* iframe: visible, big enough to be a puzzle, not the always-present reCAPTCHA badge,
// not hidden through CSS. (The old selector 'iframe[src*=captcha]' also matched the permanent
// reCAPTCHA v3 badge, so the CAPTCHA looked "unsolved" forever.)
const isChallenge = f => {
  if (!vis(f)) return false;
  const st = getComputedStyle(f);
  if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) === 0) return false;
  if (f.closest('.grecaptcha-badge')) return false;
  const r = f.getBoundingClientRect();
  if (r.width < 100 || r.height < 100) return false;
  if (r.bottom < 0 || r.right < 0) return false;
  return true;
};
const capFrames = Array.from(document.querySelectorAll(
  "iframe[src*='arkoselabs'],iframe[src*='captcha'],iframe[src*='hcaptcha'],iframe[src*='turnstile'],iframe[src*='challenges.cloudflare']"
)).filter(isChallenge);
const c = q('#total-count-id');
const inp = q("input[formcontrolname='lastName']");
return {
  captcha: capFrames.length > 0,
  capinfo: capFrames.map(f => (f.src || '').slice(0, 80)),
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
        self.page = self._ctx = self._cm = None   # camoufox (Playwright) objects
        self.loads = 0

    # ---------- lifecycle ----------
    def _pw_mode(self):
        """True when the browser is driven through Playwright (camoufox) instead of Selenium."""
        return config.BROWSER_ENGINE == "camoufox"

    def _js(self, script):
        """Run a JS snippet that may use a top-level `return` (Selenium style) on either engine."""
        if self._pw_mode():
            return self.page.evaluate("() => {" + script + "\n}")
        return self.driver.execute_script(script)

    def goto(self, url):
        if self._pw_mode():
            self.page.goto(url, wait_until="domcontentloaded")
        else:
            self.driver.get(url)

    def start(self):
        engine = config.BROWSER_ENGINE
        if engine not in ("seleniumbase", "camoufox"):
            raise SystemExit(f"BROWSER_ENGINE={engine!r} is not valid - use seleniumbase or camoufox")
        # separate profile folder per engine: a Chrome profile and a Firefox profile must never be mixed
        suffix = "" if engine == "seleniumbase" else "-" + engine
        profile = os.path.abspath(os.path.join(config.PROFILE_DIR, self.worker_id + suffix))
        os.makedirs(profile, exist_ok=True)
        if engine == "camoufox":
            return self._start_camoufox(profile)
        from seleniumbase import Driver  # imported lazily so ctl.py/seed.py don't need it
        log.info("Starting Chrome (profile %s)", profile)
        self.driver = Driver(
            uc=config.USE_UC,
            headless=False,                      # runs on the worker's Xvfb display
            user_data_dir=profile,               # cookies survive restarts -> fewer repeat prompts
            chromium_arg="--disable-dev-shm-usage",
        )
        self.loads = 0

    def _start_camoufox(self, profile):
        """Camoufox: Firefox with the fingerprint handled inside the browser, driven through Playwright.
        No stealth JavaScript is injected from here. The operator still solves every CAPTCHA by hand."""
        try:
            from camoufox.sync_api import Camoufox  # lazy: only needed for this engine
        except ImportError:
            raise SystemExit("Camoufox is missing - run: pip install -U camoufox   and then:   python -m camoufox fetch") from None
        headless = {"true": True, "virtual": "virtual"}.get(config.CAMOUFOX_HEADLESS, False)
        kwargs = dict(headless=headless, persistent_context=True, user_data_dir=profile,
                      os=config.CAMOUFOX_OS, geoip=config.CAMOUFOX_GEOIP)
        if config.CAMOUFOX_EXECUTABLE:
            kwargs["executable_path"] = config.CAMOUFOX_EXECUTABLE
        if config.CAMOUFOX_PROXY:
            from urllib.parse import urlparse
            u = urlparse(config.CAMOUFOX_PROXY)
            kwargs["proxy"] = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
            if u.username:
                kwargs["proxy"].update(username=u.username, password=u.password or "")
        log.info("Starting Camoufox (profile %s, os=%s, headless=%s, proxy=%s)", profile, config.CAMOUFOX_OS,
                 headless, "yes" if config.CAMOUFOX_PROXY else "no")
        self._cm = Camoufox(**kwargs)
        self._ctx = self._cm.__enter__()          # persistent_context=True -> a BrowserContext
        self._ctx.set_default_timeout(config.PAGE_TIMEOUT * 1000)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.driver = self.page  # truthy marker used by ensure()/close()
        self.loads = 0

    def close(self):
        if self._pw_mode():
            if self._cm is not None:
                try:
                    self._cm.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
            self._cm = self._ctx = self.page = self.driver = None
            return
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
        if self._pw_mode():
            try:
                for p in list(self._ctx.pages):
                    if p is not self.page:
                        p.close()
            except Exception as e:  # noqa: BLE001
                log.debug("tab cleanup: %s", e)
            return
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
        if self._pw_mode():
            try:
                self.page.bring_to_front()
            except Exception as e:  # noqa: BLE001
                log.debug("bring_to_front: %s", e)
            return
        try:
            self.driver.switch_to.window(self.driver.current_window_handle)
            self.driver.maximize_window()
        except Exception as e:  # noqa: BLE001
            log.debug("bring_to_front: %s", e)

    # ---------- page access ----------
    def snapshot(self):
        return self._js(SNAPSHOT_JS)

    def load(self, url, expect_name):
        """Open url and wait until the results (or empty state / CAPTCHA) are really there.

        Returns a snapshot dict. snap['captcha'] True means a CAPTCHA is on screen.
        Raises Retry on timeout.
        """
        self.ensure()
        self._single_tab()
        self.loads += 1
        self.goto(url)
        return self.wait_ready(expect_name, config.PAGE_TIMEOUT)

    def wait_ready(self, expect_name, timeout, soft=False):
        """Wait on the CURRENT page for results / empty state / CAPTCHA. soft=True returns None on timeout
        instead of raising (used right after a CAPTCHA was solved, before deciding to reload)."""
        deadline = time.time() + timeout
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
                self._js(TOS_JS)
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
        if soft:
            return None
        raise Retry(f"timeout waiting for results of {expect_name}")

    def extract(self):
        """All cases on the current page, read in one JS call."""
        self._js("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.4)
        self._js("window.scrollTo(0, 0);")
        rows = self._js(EXTRACT_JS) or []
        if any(not r["case_id"] for r in rows):  # not fully rendered yet - look once more
            time.sleep(1.5)
            rows = self._js(EXTRACT_JS) or []
        if any(not r["case_id"] for r in rows):
            raise Retry("some cases had no case id (page not fully rendered)")
        return rows
