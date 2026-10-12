#!/usr/bin/env python3
"""Run exactly ONE copy of this, on your own machine/VM (the only place that holds the WhatsApp token).

Workers never hold WhatsApp credentials: they push messages into a Redis stream and this process
forwards them to WhatsApp.     python central_notifier.py
"""
import logging
import sys
import time

import config
import taskqueue as tq
from notifier import Notifier

log = logging.getLogger("central")
EVENTS = f"{config.PREFIX}:events"
CURSOR = f"{config.PREFIX}:events:cursor"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    if not (config.WA_TOKEN and config.WA_PHONE_NUMBER_ID and config.WA_TO):
        raise SystemExit("Set WA_TOKEN, WA_PHONE_NUMBER_ID and WA_TO in .env on THIS machine.")
    if not config.REDIS_URL:
        raise SystemExit("Set REDIS_URL in .env")
    r = tq.client()
    n = Notifier("central", r)
    last = r.get(CURSOR) or "$"          # first run: only messages from now on
    n._post(f"🟢 [{config.PROJECT}] central notifier started")
    log.info("Forwarding events from %s (from %s)", EVENTS, last)
    while True:
        try:
            res = r.xread({EVENTS: last}, count=20, block=15000)
        except Exception as e:  # noqa: BLE001 - network blip: wait and retry
            log.warning("redis read failed: %s", e)
            time.sleep(5)
            continue
        for _stream, entries in res or []:
            for mid, data in entries:
                text = (data or {}).get("text", "")
                if text:
                    ok = n._post(text)
                    log.info("forwarded (%s): %s", "ok" if ok else "FAILED", text[:80].replace("\n", " "))
                last = mid
                r.set(CURSOR, last)


if __name__ == "__main__":
    main()
