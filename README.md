# Court scraper – single VM, Redis queue, Google Sheets

```
names.xlsx → seed.py → Redis streams ─┬→ worker w1 ┐
                                       ├→ worker w2 ├→ buffer 100 rows → Neon (one transaction) → ack
                                       └→ worker w3 ┘
```
`url_generator.py` is gone: a "name" task reads the total count **and** scrapes page 1, then enqueues pages 2..N itself.

## 1. One-time setup (Ubuntu 22.04/24.04, ≥4 vCPU / 8 GB for 3 workers)
```bash
bash setup_vm.sh                    # chrome, xvfb, x11vnc, redis (local, AOF on), venv
cp .env.example .env && chmod 600 .env && nano .env     # DATABASE_URL, WA_* ...
source .venv/bin/activate
python app_schema.py                # creates the sheets (see SHEETS_SETUP.md first)
```

## 2. Test before the real run (do this!)
```bash
python seed.py --file names.xlsx --column NameLead      # or make a 5-name file first
bash run_workers.sh 1                                    # one worker, watch logs/w1.log
python ctl.py status
```
Check in the DB: `SELECT * FROM v_name_progress LIMIT 20;` – `scraped_records` should equal `total_records`.
**Things I could not verify from here and you must check once:**
1. A name with **zero** results: does the page show "Total Record Count: 0" or a message? Put that text in `NO_RESULTS_TEXT`.
2. WhatsApp: send a test (`WA_MODE=text` works only within 24 h of you messaging the number; otherwise use a template).
3. A name with very many pages: does the site stop paging at some cap? Compare `total_records` vs rows saved.

## 3. Run for real
```bash
tmux new -s scraper
bash run_workers.sh 3        # start with 2, raise once CAPTCHA rate is low
# detach: Ctrl-b d      reattach: tmux attach -t scraper
```
Graceful stop: `bash stop_workers.sh` (finishes page, flushes DB, exits). Start again with `run_workers.sh` – it resumes where it left off.

## 4. CAPTCHA flow
1. Worker detects it → **all workers pause** → WhatsApp: "CAPTCHA on worker w2 … VNC port 5902".
2. In Termius add a port forward: local `5902` → remote host `127.0.0.1`, port `5902`. Open any VNC client to `localhost:5902`, solve it in that Chrome window.
3. The worker notices it is gone, waits `CAPTCHA_COOLDOWN` s, raises the delay multiplier ×1.5, and everyone resumes. Reminders every 15 min while paused.
4. Force-resume anytime: `python ctl.py resume`.

## 5. Commands
| Command | Use |
|---|---|
| `python ctl.py status` | queue, workers alive, pause state, DB totals |
| `python ctl.py pause` / `resume` | manual pause / clear pause + reset slow-down |
| `python ctl.py slow 2` | make everything 2× slower |
| `python ctl.py requeue-dead` | retry tasks that failed every attempt |
| `python ctl.py reconcile` | when the queue is empty: find pages the site reported but the DB lacks, re-queue them |

## 6. WhatsApp alerts
Start/stop, every 1000 new records (rate + remaining), CAPTCHA, final failures, DB trouble, crashes, stalls (no progress 10 min), completion; warnings arrive as a digest. Each kind has an on/off flag in `.env`. Sending never crashes a worker.

## 7. Safety nets
* Task is acked only **after** the DB commit; crashed worker → task re-claimed after 2 min. Duplicates impossible (primary key + `ON CONFLICT DO NOTHING`).
* 4 failed attempts → dead-letter (Redis + `failed_tasks` table + WhatsApp). 6 failures in a row → global pause + alert.
* Browser restarts every 300 page loads; one tab only; per-worker Chrome profile keeps cookies across restarts.
* `GLOBAL_MIN_INTERVAL` is a shared rate limit across all workers; the multiplier backs off after each CAPTCHA.
* Watch the cell count in `python ctl.py status` (a spreadsheet holds at most 10,000,000 cells, about 800k case rows) – watch `python ctl.py status` (DB size). Upgrade to pay-as-you-go before it fills.

---

# Distributed mode: many operators, each on their own PC (Windows .exe)

```
owner:      seed.py  ──►  Redis (shared queue, rate gate, events)  ◄──  central_notifier.py ──► WhatsApp
operators:  court_worker.exe (each PC) ──► pulls tasks from Redis ──► Neon (limited role) in batches of 10
```

## Owner steps (once)
1. **Limited DB role:** run `worker_role.sql` in the Neon SQL editor (set a long random password). Operators get *that* connection string, never yours.
2. **Seed the queue** from your PC (your `.env` has the Redis URL + your DB URL): `python seed.py --file names.xlsx --column NameLead`. Seeding also records the search settings (dates/filters); a worker whose settings differ refuses to start.
3. **Shared pace:** `python ctl.py rate 3` = at most one page load every 3 s across ALL operators combined (times the CAPTCHA slow-down). Ten PCs share this limit; they do not each get their own. Change it only on purpose.
4. **Alerts:** run `python central_notifier.py` on one machine that has the `WA_*` values in `.env`. Workers send alerts through Redis, so the token never leaves your side.
5. **Build the package on Windows:** `build_exe.bat` → zip `dist\court_worker\` + a filled-in `.env` per operator (see `.env.worker.example`, `WORKER_README.md`).

## How a CAPTCHA works now
`CAPTCHA_MODE=local` (default): only that operator's worker waits; the console beeps, Chrome comes to the front, the owner gets one WhatsApp alert, and the shared slow-down rises ×1.5 (fades after 1 hour). Single-VM / remote-desktop setups: set `CAPTCHA_MODE=global` in `.env` to keep the old "pause everyone" behaviour.

## Watch the owner side
`python ctl.py status` shows queue depth, workers alive, shared interval, Redis memory and DB totals. `python ctl.py reconcile` re-queues pages missing from the DB once the queue is empty.

## Known limits / not yet verified
- The packaging recipe (`build_exe.bat`) is untested; SeleniumBase + PyInstaller can need tweaks.
- Redis Cloud's free plan is small (I believe ~30 MB, check your plan). 100k+ page tasks may not fit; watch `redis memory` in `ctl.py status`. Attempt counters are now deleted after each commit to save space.
- A blocked machine does not stop itself. After a run of failures it pauses everyone. If you want it to stop immediately on a firewall block page, say so and I'll add detection.
