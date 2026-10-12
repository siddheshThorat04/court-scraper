#!/usr/bin/env python3
"""ONE page load in the real browser - tells you if this machine/IP can use the site.

    xvfb-run -a python test_one.py DAVA        (Linux VM)
    python test_one.py DAVA                     (desktop)
"""
import json
import sys
import time

import config
from browser import Browser

name = sys.argv[1] if len(sys.argv) > 1 else "DAVA"
b = Browser("test")
try:
    b.start()
    url = config.build_url(config.COURT_CODE, name, 1)
    print("Loading:", url)
    b.driver.get(url)
    time.sleep(12)
    snap = b.snapshot()
    snap["body"] = snap["body"][:400]
    print(json.dumps(snap, indent=2))
finally:
    b.close()
