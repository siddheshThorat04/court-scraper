"""WhatsApp Cloud API notifications.

* Never raises - a failed notification must not crash the scraper.
* Per-kind on/off flags (config.NOTIFY_*).
* Errors are throttled across all workers via Redis; warnings are batched into a digest.
"""
import logging
import time

import requests

import config

log = logging.getLogger("notify")

EMOJI = {"info": "🟢", "warn": "⚠️", "error": "🔴", "critical": "🚨"}

# kind -> enabled?
FLAGS = {
    "start": config.NOTIFY_START_STOP,
    "stop": config.NOTIFY_START_STOP,
    "progress": config.NOTIFY_PROGRESS,
    "captcha": config.NOTIFY_CAPTCHA,
    "error": config.NOTIFY_ERRORS,
    "db": config.NOTIFY_ERRORS,
    "crash": config.NOTIFY_ERRORS,
    "warning": config.NOTIFY_WARNINGS,
    "stall": config.NOTIFY_STALL,
    "complete": config.NOTIFY_COMPLETE,
}


class Notifier:
    def __init__(self, worker_id, redis_client=None):
        self.worker = worker_id
        self.r = redis_client
        self.warnings = []
        self.last_digest = time.time()

    # ---------- low level ----------
    def _payload(self, text):
        if config.WA_MODE == "template" and config.WA_TEMPLATE_NAME:
            # Template variables may not contain newlines or runs of spaces.
            flat = " | ".join(p.strip() for p in text.splitlines() if p.strip())[:1000]
            return {
                "messaging_product": "whatsapp",
                "to": config.WA_TO,
                "type": "template",
                "template": {
                    "name": config.WA_TEMPLATE_NAME,
                    "language": {"code": config.WA_TEMPLATE_LANG},
                    "components": [{"type": "body", "parameters": [{"type": "text", "text": flat}]}],
                },
            }
        return {
            "messaging_product": "whatsapp",
            "to": config.WA_TO,
            "type": "text",
            "text": {"body": text[:4000]},
        }

    def _post(self, text):
        if not (config.NOTIFY_ENABLED and config.WA_TOKEN and config.WA_PHONE_NUMBER_ID and config.WA_TO):
            return False
        url = f"https://graph.facebook.com/{config.WA_API_VERSION}/{config.WA_PHONE_NUMBER_ID}/messages"
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {config.WA_TOKEN}", "Content-Type": "application/json"},
                json=self._payload(text),
                timeout=10,
            )
            if resp.status_code >= 300:
                log.error("WhatsApp send failed %s: %s", resp.status_code, resp.text[:300])
                return False
            return True
        except Exception as e:  # noqa: BLE001 - must never propagate
            log.error("WhatsApp send error: %s", e)
            return False

    def _deliver(self, text):
        """Send directly if this machine has WhatsApp credentials; otherwise hand the message to Redis,
        where the single central_notifier.py process forwards it. (Keeps the token off worker machines.)"""
        if not config.NOTIFY_ENABLED:
            return False
        if config.WA_TOKEN and config.WA_PHONE_NUMBER_ID and config.WA_TO:
            return self._post(text)
        if self.r is not None:
            try:
                self.r.xadd(f"{config.PREFIX}:events", {"text": text[:3500]}, maxlen=2000, approximate=True)
                return True
            except Exception as e:  # noqa: BLE001
                log.error("could not queue notification: %s", e)
        return False

    # ---------- public API ----------
    def _emit(self, level, kind, text, key=None, throttle=0):
        log.log({"info": 20, "warn": 30, "error": 40, "critical": 50}[level], "[%s] %s", kind, text)
        if not FLAGS.get(kind, True):
            return
        suppressed = 0
        if throttle and key and self.r is not None:
            try:
                nt = f"{config.PREFIX}:nt:{key}"
                if not self.r.set(nt, 1, nx=True, ex=throttle):
                    self.r.incr(f"{config.PREFIX}:nts:{key}")
                    return
                suppressed = int(self.r.getdel(f"{config.PREFIX}:nts:{key}") or 0)
            except Exception:  # noqa: BLE001
                pass
        if suppressed:
            text += f"\n(+{suppressed} similar suppressed)"
        self._deliver(f"{EMOJI[level]} [{config.PROJECT}/{self.worker}] {text}")

    def info(self, kind, text, **kw):
        self._emit("info", kind, text, **kw)

    def error(self, kind, text, key=None, throttle=None):
        self._emit("error", kind, text, key=key or kind, throttle=config.ERROR_THROTTLE if throttle is None else throttle)

    def critical(self, kind, text, key=None, throttle=0):
        self._emit("critical", kind, text, key=key, throttle=throttle)

    def warn(self, text):
        """Batched: goes out as one digest message every WARN_DIGEST_SECONDS."""
        log.warning("%s", text)
        if FLAGS["warning"]:
            self.warnings.append(text)

    def tick(self):
        if self.warnings and time.time() - self.last_digest >= config.WARN_DIGEST_SECONDS:
            items = self.warnings[:8]
            more = len(self.warnings) - len(items)
            body = f"{len(self.warnings)} warning(s):\n" + "\n".join("• " + w for w in items)
            if more > 0:
                body += f"\n…and {more} more (see logs)"
            self._deliver(f"{EMOJI['warn']} [{config.PROJECT}/{self.worker}] {body}")
            self.warnings = []
            self.last_digest = time.time()
        elif not self.warnings:
            self.last_digest = time.time()
