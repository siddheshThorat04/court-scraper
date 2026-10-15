"""
Test: load the Windscribe extension into Playwright's Chromium (unpacked, side-loaded).

1. Put the UNPACKED extension in a folder (the one that contains manifest.json) and set EXT.
2. Run:  python test_vpn_ext.py
3. In the browser window: click the puzzle icon -> Windscribe -> log in -> connect.
4. Back in the terminal press Enter. It prints your IP so you can see if the VPN is working.
The login is kept in PROFILE, so next time you do not have to log in again.
Use a DIFFERENT PROFILE folder for every worker (Chromium locks a profile to one browser).
"""
from playwright.sync_api import sync_playwright

EXT = r"C:\vpn\windscribe"       # folder with manifest.json
PROFILE = r"C:\vpn\profile1"     # created automatically

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE,
        headless=False,                              # extensions do not load in old headless mode
        ignore_default_args=["--disable-extensions"],  # Playwright adds this flag by default
        args=[
            f"--disable-extensions-except={EXT}",
            f"--load-extension={EXT}",
        ],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://api.ipify.org")
    print("IP before VPN:", page.inner_text("body"))

    input("Log in / connect Windscribe in the browser, then press Enter here...")

    page.goto("https://api.ipify.org")
    print("IP after VPN: ", page.inner_text("body"))
    ctx.close()