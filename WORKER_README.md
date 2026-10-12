# Court scraper worker – operator guide

## Setup (once)
1. Install **Google Chrome** if you don't have it.
2. Unzip the `court_worker` folder anywhere (for example `C:\court_worker`).
3. Copy `.env.example` to `.env` (same folder) and paste in the two values you were given: `REDIS_URL` and `DATABASE_URL`. Change nothing else.

## Running
1. Double-click **court_worker.exe**. A black window and a Chrome window open. Leave both open.
2. The program works through the job by itself. You don't need to click anything.
3. **If the program beeps** and the console says *ACTION NEEDED*, a CAPTCHA is showing in the Chrome window. Solve it by hand. The program notices and carries on by itself after a short pause.
4. To stop: click the black window and press **Ctrl+C once**. Wait for it to say it has stopped. Closing the window abruptly is safe too, but unsaved work is redone later.

## Please
- Keep the PC awake and online while it runs (turn off sleep).
- Don't open the court site in other tabs of that Chrome window.
- Don't edit dates or settings. If the program says your settings differ, ask for a fresh `.env`.
- Keep the `.env` file private. It contains passwords.

## If something looks wrong
- **"Configuration problem"**: the `.env` file is missing or a value is wrong.
- **Repeated errors or "request is blocked"** in the Chrome window: close the program (Ctrl+C) and tell the owner. Do not keep retrying.
- Screenshots of the black window help us diagnose problems.
