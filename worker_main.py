"""Entry point for the packaged Windows worker (also runs as `python worker_main.py`).

Shows readable errors and keeps the window open, so a non-technical operator can read them.
"""
import sys


def pause(msg=""):
    if msg:
        print(msg)
    try:
        input("\nPress Enter to close this window...")
    except EOFError:
        pass


def main():
    try:
        import config
        config.validate()
    except SystemExit as e:
        pause(f"Configuration problem: {e}\n\nCheck the .env file next to this program.")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        pause(f"Could not start: {e}")
        sys.exit(1)

    print("=" * 64)
    print(f"  Court scraper worker  |  id: {config.WORKER_ID}")
    print("  Keep this window open. Press Ctrl+C once to stop safely.")
    print("  If a Chrome window shows a CAPTCHA, solve it - this program beeps and continues by itself.")
    print("=" * 64)

    import worker
    try:
        worker.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        if code != 0:
            pause(f"The worker stopped: {e.code}")
        sys.exit(code)
    pause("Worker stopped cleanly.")


if __name__ == "__main__":
    main()
