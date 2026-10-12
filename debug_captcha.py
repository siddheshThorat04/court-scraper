#!/usr/bin/env python3
"""Shows exactly which iframe the scraper thinks is a CAPTCHA, before and after you solve it.

    python debug_captcha.py DAVA
Solve the CAPTCHA when it appears, then watch the output: 'captcha' must flip to False.
If it stays True, the printed iframe src/size tells you what is still matching.
"""
import sys
import time

import config
from browser import Browser

LIST_JS = """return Array.from(document.querySelectorAll('iframe')).map(f => {
  const r = f.getBoundingClientRect();
  return {src: (f.src||'').slice(0,90), w: Math.round(r.width), h: Math.round(r.height),
          vis: f.offsetWidth>0 && f.offsetHeight>0, badge: !!f.closest('.grecaptcha-badge')};
});"""

name = sys.argv[1] if len(sys.argv) > 1 else "DAVA"
b = Browser("debug")
try:
    b.start()
    b.driver.get(config.build_url(config.COURT_CODE, name, 1))
    for i in range(60):
        time.sleep(3)
        snap = b.snapshot()
        print(f"[{i*3:>3}s] captcha={snap['captcha']} matched={snap.get('capinfo')} "
              f"count={snap['count']} items={snap['items']}")
        for f in b.driver.execute_script(LIST_JS):
            print("       iframe:", f)
finally:
    b.close()
