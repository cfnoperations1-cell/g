"""Search the web through whichever provider has a key in .env.

Standard library only. Every provider is optional: with no key at all, search()
raises NoProvider so the caller can fall back to a human- or agent-driven search
instead of silently returning nothing.

Add ONE of these to /home/user/g/.env to turn automated discovery on:

    SERPER_API_KEY=...            # serper.dev, Google results, easiest to get
    BRAVE_SEARCH_API_KEY=...      # brave.com/search/api, independent index
    GOOGLE_CSE_API_KEY=...        # console.cloud.google.com + a Programmable
    GOOGLE_CSE_CX=...             #   Search engine set to search the whole web

    python3 scraper/search_api.py "med spa semaglutide Dallas"   # smoke test
"""
import json, os, ssl, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CTX = ssl.create_default_context()


class NoProvider(RuntimeError):
    pass


def env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v.strip()
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, val = line.split("=", 1)
                if k.strip() == key:
                    return val.strip().strip('"').strip("'")
    return default


def _get(url, headers=None, data=None, method=None, timeout=25):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return json.loads(r.read())


def provider():
    """Which provider is configured, most convenient first."""
    if env("SERPER_API_KEY"):
        return "serper"
    if env("BRAVE_SEARCH_API_KEY"):
        return "brave"
    if env("GOOGLE_CSE_API_KEY") and env("GOOGLE_CSE_CX"):
        return "cse"
    return ""


def search(query, count=10):
    """Return [(title, url)] for one query. Raises NoProvider when no key is set."""
    p = provider()
    if not p:
        raise NoProvider("no search key in .env (SERPER_API_KEY, BRAVE_SEARCH_API_KEY, "
                         "or GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX)")
    if p == "serper":
        d = _get("https://google.serper.dev/search",
                 {"X-API-KEY": env("SERPER_API_KEY"), "Content-Type": "application/json"},
                 json.dumps({"q": query, "num": count}).encode(), "POST")
        return [(o.get("title", ""), o.get("link", "")) for o in d.get("organic", [])]
    if p == "brave":
        u = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
            {"q": query, "count": count, "country": "US"})
        d = _get(u, {"X-Subscription-Token": env("BRAVE_SEARCH_API_KEY"),
                     "Accept": "application/json"})
        return [(o.get("title", ""), o.get("url", "")) for o in d.get("web", {}).get("results", [])]
    u = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(
        {"key": env("GOOGLE_CSE_API_KEY"), "cx": env("GOOGLE_CSE_CX"), "q": query,
         "num": min(count, 10), "gl": "us"})
    d = _get(u)
    return [(o.get("title", ""), o.get("link", "")) for o in d.get("items", [])]


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "med spa semaglutide Dallas"
    print("provider:", provider() or "(none configured)")
    try:
        for t, u in search(q):
            print(f"  {t[:60]:<62} {u}")
    except NoProvider as e:
        print("NoProvider:", e)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}")
