"""Redis-backed task queue.

* Redis Streams + consumer group  -> a task whose worker dies stays "pending" and is re-claimed.
* A Lua script does "dedupe + enqueue" atomically -> no duplicates, no lost tasks.
* Tasks are acknowledged only AFTER the DB commit (at-least-once; DB key makes it idempotent).
* Named taskqueue.py (not queue.py) so it never shadows Python's stdlib `queue`.
"""
import json
import time

import redis

import config


def K(suffix):
    return f"{config.PREFIX}:{suffix}"


S_NAME = K("stream:name")      # stage 1: "count this name + scrape its page 1"
S_PAGE = K("stream:page")      # stage 2: "scrape page N of this name"
S_DEAD = K("stream:dead")      # dead letters
SEEN_NAME = K("seen:name")
SEEN_PAGE = K("seen:page")
GROUP = "workers"
ATTEMPTS = K("attempts")
PAUSE = K("ctl:pause")
STOP = K("ctl:stop")
MULT = K("rate:mult")
GATE = K("rate:gate")
TOTAL = K("stats:records")
LAST_PROGRESS = K("stats:last_progress_ts")
START_TS = K("stats:start_ts")
COMPLETE = K("notified:complete")

# ---- census / progress counters (see progress.py) ----
C_PAIRS_TOTAL = K("census:pairs_total")   # (court, name) pairs seeded
C_PAIRS_DONE = K("census:pairs_done")     # pairs whose page 1 has been read (total count known)
C_PAIRS_ZERO = K("census:pairs_zero")     # ... of which had zero records
C_RECORDS = K("census:records")           # sum of "Total Record Count" over counted pairs
C_PAGES = K("census:pages")               # sum of page counts (incl. page 1) over non-empty counted pairs
C_SEEN = K("census:seen")                 # set of counted pairs -> each pair is counted once, even if retried
P_LOADS = K("progress:loads_done")        # tasks finished (census loads + page loads)
P_PAGES = K("progress:pages_done")        # data pages saved
CAPTCHA_COUNT = K("captcha:count")        # hash: worker/machine id -> number of CAPTCHAs it hit
P_SAMPLES = K("progress:samples")         # zset of "ts:loads_done" samples -> recent speed for the ETA

_ENQUEUE_LUA = """
local n = 0
for i = 1, #ARGV, 2 do
  if redis.call('SADD', KEYS[1], ARGV[i]) == 1 then
    redis.call('XADD', KEYS[2], '*', 'd', ARGV[i + 1])
    n = n + 1
  end
end
return n
"""


def client():
    return redis.Redis.from_url(config.REDIS_URL, decode_responses=True)


def ensure_groups(r):
    for stream in (S_NAME, S_PAGE):
        try:
            r.xgroup_create(stream, GROUP, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise


def task_id(t):
    return f"{t['stage']}|{t['court']}|{t['name']}|{t.get('page', 1)}"


def _enqueue(r, seen_key, stream, items):
    """items = [(seen_member, task_dict), ...]; returns number actually enqueued."""
    if not items:
        return 0
    argv = []
    for member, task in items:
        argv += [member, json.dumps(task)]
    return int(r.register_script(_ENQUEUE_LUA)(keys=[seen_key, stream], args=argv))


def enqueue_names(r, court, names):
    items = [(f"{court}|{n}", {"stage": "name", "court": court, "name": n, "page": 1}) for n in names]
    return _enqueue(r, SEEN_NAME, S_NAME, items)


def enqueue_pairs(r, pairs):
    """pairs = [(court, name), ...] - any mix of courts and names; returns number actually enqueued."""
    items = [(f"{c}|{n}", {"stage": "name", "court": c, "name": n, "page": 1}) for c, n in pairs]
    return _enqueue(r, SEEN_NAME, S_NAME, items)


_CENSUS_LUA = """
if redis.call('SADD', KEYS[1], ARGV[1]) == 1 then
  redis.call('INCR', KEYS[2])
  if tonumber(ARGV[2]) == 0 then
    redis.call('INCR', KEYS[3])
  else
    redis.call('INCRBY', KEYS[4], ARGV[2])
    redis.call('INCRBY', KEYS[5], ARGV[3])
  end
  return 1
end
return 0
"""


def record_census(r, court, name, total, pages):
    """Count one (court, name) pair exactly once: its total records and page count are now known."""
    return int(r.register_script(_CENSUS_LUA)(
        keys=[C_SEEN, C_PAIRS_DONE, C_PAIRS_ZERO, C_RECORDS, C_PAGES],
        args=[f"{court}|{name}", int(total), int(pages)]))


def record_progress(r, loads, pages):
    """Called after a batch is committed: `loads` tasks finished, `pages` data pages saved."""
    now = time.time()
    pipe = r.pipeline()
    pipe.incrby(P_LOADS, int(loads))
    pipe.incrby(P_PAGES, int(pages))
    done = pipe.execute()[0]
    r.zadd(P_SAMPLES, {f"{int(now)}:{done}": now})
    r.zremrangebyscore(P_SAMPLES, 0, now - 7200)


def enqueue_pages(r, court, name, pages, total):
    items = [(f"{court}|{name}|{p}",
              {"stage": "page", "court": court, "name": name, "page": p, "total": total}) for p in pages]
    return _enqueue(r, SEEN_PAGE, S_PAGE, items)


def fetch(r, consumer):
    """Next task as (stream, msg_id, task) or None. Stale/failed tasks before new ones.
    CENSUS_FIRST (default on): every (court, name) count task before any page task, so all totals are known
    early and the progress / ETA is meaningful. Set CENSUS_FIRST=0 in .env for the old pages-first order."""
    census_first = getattr(config, "CENSUS_FIRST", True)
    for stream in ((S_NAME, S_PAGE) if census_first else (S_PAGE, S_NAME)):
        try:
            res = r.xautoclaim(stream, GROUP, consumer, min_idle_time=config.RETRY_IDLE_MS,
                               start_id="0-0", count=1)
            msgs = res[1] if res else []
        except redis.ResponseError:
            msgs = []
        for mid, data in msgs:
            if data:
                return stream, mid, json.loads(data["d"])
        got = r.xreadgroup(GROUP, consumer, {stream: ">"}, count=1)
        if got:
            _, entries = got[0]
            if entries:
                mid, data = entries[0]
                return stream, mid, json.loads(data["d"])
    return None


def ack(r, stream, ids):
    if ids:
        r.xack(stream, GROUP, *ids)


def dead_letter(r, stream, mid, task, error):
    r.xadd(S_DEAD, {"d": json.dumps({"task": task, "error": str(error)[:500], "stream": stream})})
    r.xack(stream, GROUP, mid)


def depth(r):
    """{'name': (pending, lag), 'page': (pending, lag)} - lag = not yet delivered."""
    out = {}
    for label, stream in (("name", S_NAME), ("page", S_PAGE)):
        pending = lag = 0
        try:
            for g in r.xinfo_groups(stream):
                if g["name"] == GROUP:
                    pending = int(g.get("pending") or 0)
                    lag = int(g.get("lag") or 0)
        except redis.ResponseError:
            pass
        out[label] = (pending, lag)
    return out


def backlog(r):
    d = depth(r)
    return sum(p + l for p, l in d.values())


# ---------------- control flags ----------------
def get_pause(r):
    v = r.get(PAUSE)
    if not v:
        return None
    try:
        return json.loads(v)
    except ValueError:
        return {"kind": "manual", "worker": "?", "reason": v, "ts": time.time()}


def set_pause(r, kind, worker, reason):
    r.set(PAUSE, json.dumps({"kind": kind, "worker": worker, "reason": reason, "ts": time.time()}))


def clear_pause(r, owner=None):
    p = get_pause(r)
    if p and (owner is None or p.get("worker") == owner):
        r.delete(PAUSE)
        return True
    return False


def get_mult(r):
    try:
        return max(1.0, float(r.get(MULT) or 1.0))
    except ValueError:
        return 1.0
