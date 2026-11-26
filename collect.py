"""Collects mentions on a server or desktop (no browser limits, so Google News is included too).

  python collect.py "Brand name" [--days 7]     print a report in the terminal
  python collect.py --watchlist                  update data/ for every name in watchlist.json
                                                 (the scheduled GitHub Actions job runs this)
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import monitor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
UA = "brand-monitor/1.0 (+https://github.com/Knarf24/brand-monitor)"
PAGE_ORIGIN = "https://knarf24.github.io"


def fetch(url, timeout=25):
    """Returns (text or 'ERROR: ...', allows_browser) for one source link."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Origin": PAGE_ORIGIN,
                                               "Accept": "application/json, application/rss+xml, */*"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                allow = r.headers.get("Access-Control-Allow-Origin", "")
                return r.read().decode("utf-8", "replace"), allow in ("*", PAGE_ORIGIN)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(6)
                continue
            allow = e.headers.get("Access-Control-Allow-Origin", "") if e.headers else ""
            return "ERROR: the site answered %s %s" % (e.code, e.reason), allow in ("*", PAGE_ORIGIN)
        except Exception as e:
            return "ERROR: %s" % str(e)[:150], False
    return "ERROR: too many requests", False


def collect(name, days=7):
    links = monitor.plan(name, days, where="server")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda s: fetch(s["url"]), links))
    raw = {s["id"]: r[0] for s, r in zip(links, results)}
    browser_ok = {s["id"]: r[1] for s, r in zip(links, results)}
    rep = monitor.analyze(name, raw, days)
    return rep, browser_ok


def print_report(rep):
    print("\n%s: mood %s/100 (%s), %d mentions in %d days" % (rep["name"], rep["mood"], rep["verdict"],
                                                                rep["mentions_total"], rep["days"]))
    s = rep["shares"]
    print("  positive %.0f%%  neutral %.0f%%  negative %.0f%%" % (s["positive"], s["neutral"], s["negative"]))
    for key, st in rep["statuses"].items():
        print("  %-9s %s" % (key, ("%d mentions" % st["found"]) if st["ok"] else "failed: " + st["note"]))
    for a in rep["alerts"]:
        print("  ! " + a)
    if rep["praise_themes"]:
        print("  praised for:   " + ", ".join(t["word"] for t in rep["praise_themes"]))
    if rep["complaint_themes"]:
        print("  complaints:    " + ", ".join(t["word"] for t in rep["complaint_themes"]))
    for title, items in (("Most positive", rep["praise"]), ("Most negative", rep["critical"])):
        if items:
            print("  " + title + ":")
            for m in items[:3]:
                print("    %+.2f  %s" % (m["compound"], (m["title"] or m["text"])[:110]))


def update_watchlist(days=7):
    with open(os.path.join(HERE, "watchlist.json")) as f:
        names = json.load(f)["names"]
    os.makedirs(DATA, exist_ok=True)
    index, browser_seen = [], {}
    for name in names:
        rep, browser_ok = collect(name, days)
        for k, v in browser_ok.items():
            browser_seen[k] = browser_seen.get(k, False) or v
        path = os.path.join(DATA, monitor.slugify(name) + ".json")
        history = []
        if os.path.exists(path):
            with open(path) as f:
                history = json.load(f).get("history", [])
        if rep["mentions_total"] > 0:
            history = monitor.add_history(history, rep)
        rep["mentions"] = rep["mentions"][:150]  # keep the files small
        with open(path, "w") as f:
            json.dump({"report": rep, "history": history}, f, separators=(",", ":"))
        index.append({"name": name, "slug": monitor.slugify(name), "updated": rep["generated"], "mood": rep["mood"],
                      "verdict": rep["verdict"], "mentions": rep["mentions_total"],
                      "trend": [h["mood"] for h in history[-20:]]})
        print_report(rep)
        time.sleep(8)  # be gentle: GDELT allows about one search every 5 seconds
    with open(os.path.join(DATA, "index.json"), "w") as f:
        json.dump({"updated": int(time.time()), "days": days, "browser_sources": browser_seen, "brands": index},
                  f, indent=1)
    print("\nSources a web page can call directly:", browser_seen)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="brand, product or person to check")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--watchlist", action="store_true", help="update data/ for watchlist.json")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    a = ap.parse_args()
    if a.watchlist:
        update_watchlist(a.days)
    elif a.name:
        rep, _ = collect(a.name, a.days)
        print(json.dumps(rep, indent=1) if a.json else "", end="")
        if not a.json:
            print_report(rep)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
