"""Google Sheets storage through the Apps Script web app (apps_script/Code.gs).

Same interface the worker always used (write / record_failed / done_names / missing_pages / stats), but the
data goes over HTTPS (port 443), so a VPN or firewall does not get in the way.

Sheets has no unique keys, so duplicates are prevented in two layers:
  1. Redis set "done": a page (or name summary) already stored by any worker is skipped.
  2. A deterministic batch id: if a request succeeded but its reply was lost, the retry is ignored by the script.
A crash in the tiny window between "sheet written" and "marked done" can still repeat a page - dedupe on
(court_code, case_id, party_name, party_type) when you export.
"""
import hashlib
import json
import logging

import requests

import config
import taskqueue as tq

log = logging.getLogger("db")

CELL_LIMIT = 10_000_000


class DB:
    def __init__(self, url=None, secret=None):
        self.url = url or config.SHEETS_URL
        self.secret = secret or config.SHEETS_SECRET
        self._r = None

    # ---------- plumbing ----------
    def _redis(self):
        if self._r is None:
            self._r = tq.client()
        return self._r

    def _call(self, action, **payload):
        body = json.dumps({"secret": self.secret, "action": action, **payload})
        # The web app answers a POST with a redirect to the result page; requests follows it.
        resp = requests.post(self.url, data=body.encode("utf-8"), timeout=90,
                             headers={"Content-Type": "text/plain; charset=utf-8"})
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError("Sheets web app did not return JSON (is it deployed with access 'Anyone'?): "
                               + resp.text[:120].replace("\n", " "))
        if not data.get("ok"):
            raise RuntimeError(f"Sheets web app error: {data.get('error')}")
        return data

    def ensure(self):
        self._call("ping")
        return self

    def reset(self):
        pass                      # stateless over HTTPS - nothing to reconnect

    # ---------- writes ----------
    @staticmethod
    def _page_key(court, name, page):
        return f"page|{court}|{name}|{page}"

    @staticmethod
    def _name_key(court, name):
        return f"name|{court}|{name}"

    def write(self, cases, pages, summaries):
        """Append a batch. Returns the number of case rows sent."""
        r = self._redis()
        done_key = tq.K("done")
        page_keys = [self._page_key(p[0], p[1], p[2]) for p in pages]
        name_keys = [self._name_key(s[0], s[1]) for s in summaries]
        pipe = r.pipeline()
        for k in page_keys + name_keys:
            pipe.sismember(done_key, k)
        flags = pipe.execute()
        page_done = dict(zip(page_keys, flags[:len(page_keys)]))
        name_done = dict(zip(name_keys, flags[len(page_keys):]))

        new_pages = [p for p in pages if not page_done[self._page_key(p[0], p[1], p[2])]]
        new_sums = [s for s in summaries if not name_done[self._name_key(s[0], s[1])]]
        new_cases = [c for c in cases if not page_done.get(self._page_key(c[0], c[10], c[11]), False)]

        if new_cases or new_pages or new_sums:
            # Same content -> same id, so a retry after a lost reply is recognised by the script.
            batch_id = hashlib.sha1(json.dumps([new_cases, new_pages, new_sums], sort_keys=True,
                                               default=str).encode()).hexdigest()
            self._call("write", batch_id=batch_id, cases=[list(c) for c in new_cases],
                       pages=[list(p) for p in new_pages], summaries=[list(s) for s in new_sums])
            marks = [self._page_key(p[0], p[1], p[2]) for p in new_pages] + \
                    [self._name_key(s[0], s[1]) for s in new_sums]
            if marks:
                r.sadd(done_key, *marks)
        return len(new_cases)

    def record_failed(self, task_key, payload, error, attempts):
        self._call("failed", task_key=task_key[:400], payload=json.dumps(payload)[:2000],
                   error=str(error)[:500], attempts=attempts)

    # ---------- reads (names / pages sheets only) ----------
    def _read(self, sheet, cols):
        return self._call("read", sheet=sheet, cols=cols)["rows"]

    @staticmethod
    def _int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    def _latest_names(self, court):
        """last_name -> (total_records, total_pages); later rows win."""
        out = {}
        for row in self._read("names", 4):
            if str(row[0]) == court:
                out[str(row[1])] = (self._int(row[2]), self._int(row[3]))
        return out

    def done_names(self, court):
        return set(self._latest_names(court))

    def missing_pages(self, court):
        """(name, page, total_records) for every page the site reported but the sheet does not hold."""
        names = self._latest_names(court)
        have = {}
        for row in self._read("pages", 3):
            if str(row[0]) == court:
                have.setdefault(str(row[1]), set()).add(self._int(row[2]))
        out = []
        for name in sorted(names):
            total, total_pages = names[name]
            got = have.get(name, set())
            out += [(name, p, total) for p in range(1, total_pages + 1) if p not in got]
        return out

    def stats(self, court):
        s = self._call("stats")
        names = self._latest_names(court)
        return {"cases": s["cases"], "names_counted": len(names),
                "expected_records": sum(t for t, _ in names.values()),
                "failed_tasks": s["failed"],
                "db_size": f"{s['cells']:,} of {CELL_LIMIT:,} Google Sheets cells"}
