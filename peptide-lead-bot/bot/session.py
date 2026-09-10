"""Manual-login session capture for directory sites that gate their listings.

Usage:  python -m bot login thepeptidelist.com
A real Chromium window opens. If config/credentials.json has an entry for the domain,
the email/password fields are pre-filled. YOU click submit / solve any CAPTCHA.
When you land on the logged-in page, press Enter in the terminal and the cookies are saved
to data/sessions/<domain>.json and reused automatically by the crawler.
"""
import json
import os

SESS_DIR = "data/sessions"


def session_path(domain):
    return os.path.join(SESS_DIR, f"{domain}.json")


def load_credentials():
    p = "config/credentials.json"
    if os.path.exists(p):
        with open(p) as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    return {}


def login(domain: str):
    from playwright.sync_api import sync_playwright
    os.makedirs(SESS_DIR, exist_ok=True)
    creds = load_credentials().get(domain, {})
    url = creds.get("login_url") or f"https://{domain}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        if creds.get("email"):
            for sel in ["input[type=email]", "input[name*=email i]", "input[name*=user i]", "input[id*=email i]"]:
                try:
                    page.fill(sel, creds["email"], timeout=2000)
                    break
                except Exception:
                    pass
        if creds.get("password"):
            try:
                page.fill("input[type=password]", creds["password"], timeout=2000)
            except Exception:
                pass
        print(f"\nBrowser open at {url}. Log in (submit the form, solve any CAPTCHA), then press Enter here...")
        input()
        ctx.storage_state(path=session_path(domain))
        print(f"Saved session -> {session_path(domain)}")
        browser.close()
