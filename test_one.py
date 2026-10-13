#!/usr/bin/env python3
"""ONE page load in the real browser - tells you if this machine/IP can use the site.

    xvfb-run -a python test_one.py DAVA        (Linux VM)
    python test_one.py DAVA                     (desktop)
    python test_one.py DAVA 1800                (wait up to 30 min for you to solve a CAPTCHA)

Works with every BROWSER_ENGINE. If a CAPTCHA appears, solve it by hand in the browser window; the page is
read again afterwards. The browser stays open until you press Enter in this terminal.
"""
import json
import logging
import sys
import time

import config
from browser import Browser

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
name = sys.argv[1] if len(sys.argv) > 1 else "DAVA"
max_wait = int(sys.argv[2]) if len(sys.argv) > 2 else 600

WEBDRIVER_JS = """return {
  value: String(navigator.webdriver),
  in_navigator: 'webdriver' in navigator,
  own_property: Object.getOwnPropertyNames(navigator).includes('webdriver'),
  user_agent: navigator.userAgent
};"""

b = Browser("test")
try:
    b.start()
    url = config.build_url(config.COURT_CODE, name, 1)
    print("Engine:", config.BROWSER_ENGINE)
    print("Loading:", url)
    b.goto(url)

    deadline = time.time() + max_wait
    told = False
    while time.time() < deadline:
        time.sleep(3)
        try:
            snap = b.snapshot()
        except Exception as e:  # noqa: BLE001 - page mid-navigation
            print("  (page busy:", str(e).splitlines()[0], ")")
            continue
        if snap["captcha"]:
            if not told:
                print(">>> CAPTCHA is on screen - solve it in the browser window. Waiting...")
                told = True
            continue
        if snap["count"] or snap["items"] or "failed captcha verification" in (snap["body"] or ""):
            break

    try:
        snap = b.snapshot()
        snap["navigator"] = b._js(WEBDRIVER_JS)
    except Exception as e:  # noqa: BLE001
        snap = {"error": str(e)}
    snap["body"] = (snap.get("body") or "")[:400]
    print(json.dumps(snap, indent=2))
    input("\nDone. The browser stays open - press Enter here to close it ... ")
finally:
    b.close()
