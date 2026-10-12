"""All settings come from environment variables / the .env file. No secrets in code."""
import os
import re
import socket
import sys
import uuid
from pathlib import Path
from urllib.parse import urlencode, quote

from dotenv import load_dotenv

# Packaged .exe: the .env sits next to the program. Otherwise next to the source / current folder.
BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
load_dotenv()


def _s(name, default=""):
    return os.getenv(name, str(default)).strip()


def _i(name, default):
    return int(_s(name, default))


def _f(name, default):
    return float(_s(name, default))


def _b(name, default):
    return _s(name, default).lower() in ("1", "true", "yes", "y", "on")


def _l(name, default=""):
    return [x.strip() for x in _s(name, default).split(",") if x.strip()]


def _auto_worker_id():
    """hostname + 4 random hex; remembered so the same machine keeps the same id."""
    f = Path.home() / ".court_scraper" / "worker_id"
    try:
        if f.exists():
            return f.read_text().strip()
        wid = re.sub(r"[^A-Za-z0-9_-]", "-", socket.gethostname())[:12] + "-" + uuid.uuid4().hex[:4]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(wid)
        return wid
    except OSError:
        return "w1"


# ---------------- infrastructure ----------------
SHEETS_URL = _s("SHEETS_URL")                     # Apps Script web-app URL (see apps_script/Code.gs)
SHEETS_SECRET = _s("SHEETS_SECRET")
REDIS_URL = _s("REDIS_URL", "redis://127.0.0.1:6379/0")
PREFIX = _s("REDIS_PREFIX", "mi")
PROJECT = _s("PROJECT_NAME", "court-scraper")
WORKER_ID = _s("WORKER_ID") or _auto_worker_id()
# "local": only the worker that hit the CAPTCHA waits - the operator at THAT machine solves it.
# "global": every worker pauses (single VM / remote-desktop setups).
CAPTCHA_MODE = _s("CAPTCHA_MODE", "local").lower()
CAPTCHA_HINT = _s("CAPTCHA_HINT", "")      # replaces the VNC text in the CAPTCHA alert (e.g. AnyDesk steps)
VNC_PORT = _s("VNC_PORT", "")          # shown in the CAPTCHA alert so you know which port to open

# ---------------- what to scrape ----------------
COURT_CODE = _s("COURT_CODE", "D57")
BASE = "https://micourt.courts.michigan.gov/case-search/court"
CASE_STATUSES = _l("CASE_STATUSES", "adjudicated,disposed,closed")
CASE_TYPE_SUBCATEGORY = _s("CASE_TYPE_SUBCATEGORY", "1")
DATE_FROM = _s("DATE_FROM", "2024-10-01T18:30:00.000Z")
DATE_TO = _s("DATE_TO", "2025-10-01T18:30:00.000Z")
PAGE_SIZE = _i("PAGE_SIZE", 10)
WARN_PAGES = _i("WARN_PAGES", 200)     # warn if one name has more pages than this (possible result cap)

# Text that means "search worked but there are zero results". Verify on a real empty name!
NO_RESULTS_TEXT = [t.lower() for t in _l("NO_RESULTS_TEXT", "no results,no records,no cases found,0 results")]

# ---------------- pacing ----------------
GLOBAL_MIN_INTERVAL = _f("GLOBAL_MIN_INTERVAL", 3.0)   # min seconds between ANY two page loads (all workers)
MIN_DELAY = _f("MIN_DELAY", 3.0)                       # extra per-worker pause after each task
MAX_DELAY = _f("MAX_DELAY", 6.0)
MAX_MULT = _f("MAX_MULT", 4.0)                         # slow-down cap after repeated CAPTCHAs
CAPTCHA_COOLDOWN = _i("CAPTCHA_COOLDOWN", 90)          # seconds to wait after a CAPTCHA is solved

# ---------------- retries ----------------
MAX_ATTEMPTS = _i("MAX_ATTEMPTS", 4)
RETRY_IDLE_MS = _i("RETRY_IDLE_MS", 120000)            # un-acked task is retried after this idle time
CONSEC_FAIL_LIMIT = _i("CONSEC_FAIL_LIMIT", 6)         # this many failures in a row -> pause everything
PAGE_TIMEOUT = _i("PAGE_TIMEOUT", 45)

# ---------------- batching ----------------
BATCH_SIZE = _i("BATCH_SIZE", 100)                     # flush to DB at this many rows ...
FLUSH_SECONDS = _i("FLUSH_SECONDS", 30)                # ... or this many seconds, whichever is first

# ---------------- browser ----------------
USE_UC = _b("USE_UC", True)
RECYCLE_AFTER = _i("RECYCLE_AFTER", 300)               # restart Chrome after N page loads (frees RAM)
PROFILE_DIR = _s("PROFILE_DIR", str(Path.home() / ".court_scraper" / "profiles"))            # per-worker Chrome profile keeps cookies across restarts

# ---------------- notifications (WhatsApp Cloud API) ----------------
NOTIFY_ENABLED = _b("NOTIFY_ENABLED", True)
NOTIFY_START_STOP = _b("NOTIFY_START_STOP", True)
NOTIFY_PROGRESS = _b("NOTIFY_PROGRESS", True)
NOTIFY_CAPTCHA = _b("NOTIFY_CAPTCHA", True)
NOTIFY_ERRORS = _b("NOTIFY_ERRORS", True)
NOTIFY_WARNINGS = _b("NOTIFY_WARNINGS", True)
NOTIFY_STALL = _b("NOTIFY_STALL", True)
NOTIFY_COMPLETE = _b("NOTIFY_COMPLETE", True)
PROGRESS_EVERY = _i("PROGRESS_EVERY", 1000)            # notify after every N newly saved records
ERROR_THROTTLE = _i("ERROR_THROTTLE", 120)             # same error kind at most once per N seconds
WARN_DIGEST_SECONDS = _i("WARN_DIGEST_SECONDS", 600)
PAUSE_REMINDER_SECONDS = _i("PAUSE_REMINDER_SECONDS", 900)
STALL_MINUTES = _i("STALL_MINUTES", 10)

WA_TOKEN = _s("WA_TOKEN")
WA_PHONE_NUMBER_ID = _s("WA_PHONE_NUMBER_ID")
WA_TO = _s("WA_TO")
WA_API_VERSION = _s("WA_API_VERSION", "v20.0")
WA_MODE = _s("WA_MODE", "text")                        # "text" (24h window) or "template"
WA_TEMPLATE_NAME = _s("WA_TEMPLATE_NAME", "")          # template with ONE body variable {{1}}
WA_TEMPLATE_LANG = _s("WA_TEMPLATE_LANG", "en_US")
HEALTHCHECK_URL = _s("HEALTHCHECK_URL")                # optional healthchecks.io ping URL (dead-VM alert)


def build_url(court, name, page):
    """Search URL for one page. Names are percent-encoded (spaces -> %20)."""
    params = [("lastName", name)]
    params += [("caseStatus", s) for s in CASE_STATUSES]
    params += [
        ("caseTypeSubCategory", CASE_TYPE_SUBCATEGORY),
        ("filedDateFrom", DATE_FROM),
        ("filedDateTo", DATE_TO),
        ("page", page),
    ]
    return f"{BASE}/{court}/search?" + urlencode(params, quote_via=quote, safe=":")


def validate():
    missing = [n for n, v in (("SHEETS_URL", SHEETS_URL), ("SHEETS_SECRET", SHEETS_SECRET), ("REDIS_URL", REDIS_URL)) if not v]
    if missing:
        raise SystemExit("Missing config: " + ", ".join(missing))


def fingerprint():
    """Hash of everything that changes WHICH records a URL returns. All workers must match."""
    import hashlib
    raw = "|".join([",".join(CASE_STATUSES), CASE_TYPE_SUBCATEGORY, DATE_FROM, DATE_TO, str(PAGE_SIZE)])
    return hashlib.sha1(raw.encode()).hexdigest()[:12]
