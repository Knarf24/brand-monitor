"""Brand Reputation Monitor: what are people saying about a name, and how do they feel about it?

The same Python runs in three places:
  * in the browser (Pyodide) on the live web page,
  * in the scheduled GitHub Actions job (collect.py) that tracks a watchlist over time,
  * on a desktop from the command line:  python collect.py "Brand name"

What it does
  1. plan()      builds the search links for each free source (news, Reddit, Hacker News, Bluesky, Mastodon).
  2. parse_*()   turns each source's raw reply into the same simple "mention" records.
  3. score()     rates each mention from -1 (angry) to +1 (happy) with VADER, a sentiment model made for
                 short social media text, plus extra business-news words VADER does not know
                 (recall, lawsuit, outage, layoffs, ...).
  4. report()    sums it all up: a 0-100 mood score, positive / neutral / negative shares, mood per source
                 and per day, the most praising and most critical mentions, what people praise and complain
                 about, and an early-warning note when complaints suddenly rise.

Combines three GeeksforGeeks projects: Twitter Sentiment Analysis (VADER scoring), Scraping Reddit
(reddit JSON) and Scrape Most Reviewed News (news headlines).
"""

import html
import json
import math
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.parse import quote, quote_plus
from xml.etree import ElementTree

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

VERSION = "1.0"

# ---------------------------------------------------------------- sources

SOURCES = {
    "news": {"label": "News (GDELT)", "icon": "📰", "home": "https://www.gdeltproject.org/"},
    "gnews": {"label": "Google News", "icon": "🗞️", "home": "https://news.google.com/"},
    "reddit": {"label": "Reddit", "icon": "👽", "home": "https://www.reddit.com/"},
    "hn": {"label": "Hacker News", "icon": "🟧", "home": "https://news.ycombinator.com/"},
    "bluesky": {"label": "Bluesky", "icon": "🦋", "home": "https://bsky.app/"},
    "mastodon": {"label": "Mastodon", "icon": "🐘", "home": "https://mastodon.social/"},
    "pasted": {"label": "Your own text", "icon": "📋", "home": ""},
}

# Sources a web page may call directly. Google News has no "allow other websites" header, so it is
# only read by the scheduled job (which runs on a server, not in a browser).
BROWSER_SOURCES = ["news", "reddit", "hn", "bluesky", "mastodon"]
SERVER_SOURCES = ["news", "gnews", "reddit", "hn", "bluesky", "mastodon"]


def clean_name(name):
    name = re.sub(r"\s+", " ", (name or "")).strip().strip('"').strip()
    return name[:60]


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", clean_name(name).lower()).strip("-") or "brand"


def plan(name, days=7, where="browser"):
    """The search links to fetch for a name, one per source."""
    name = clean_name(name)
    days = max(1, min(int(days), 30))
    since = int(time.time()) - days * 86400
    since_iso = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    phrase = '"%s"' % name
    tag = re.sub(r"[^A-Za-z0-9_]", "", name)
    reddit_t = "day" if days <= 1 else "week" if days <= 7 else "month"
    links = {
        # GDELT watches news sites worldwide; quotes keep multi-word names together.
        "news": "https://api.gdeltproject.org/api/v2/doc/doc?query=%s&mode=artlist&format=json"
                "&maxrecords=75&sort=datedesc&timespan=%dd" % (quote(phrase + " sourcelang:english"), days),
        "gnews": "https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en"
                 % quote_plus("%s when:%dd" % (phrase, days)),
        "reddit": "https://www.reddit.com/search.json?q=%s&sort=new&t=%s&limit=75&raw_json=1"
                  % (quote(phrase), reddit_t),
        "hn": "https://hn.algolia.com/api/v1/search_by_date?query=%s&tags=(story,comment)"
              "&numericFilters=created_at_i%%3E%d&hitsPerPage=75" % (quote(phrase), since),
        "bluesky": "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=%s&sort=latest&limit=75&since=%s"
                   % (quote(phrase), quote(since_iso)),
        "mastodon": ("https://mastodon.social/api/v1/timelines/tag/%s?limit=40" % quote(tag)) if len(tag) >= 2 else "",
    }
    wanted = BROWSER_SOURCES if where == "browser" else SERVER_SOURCES
    out = []
    for key in wanted:
        if links.get(key):
            out.append({"id": key, "label": SOURCES[key]["label"], "icon": SOURCES[key]["icon"], "url": links[key]})
    return out


def plan_json(name, days=7, where="browser"):
    return json.dumps(plan(name, days, where))


# ---------------------------------------------------------------- parsing

TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"https?://\S+")
SPACE_RE = re.compile(r"\s+")


def strip_html(text):
    text = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", " ", text or "", flags=re.I)
    return SPACE_RE.sub(" ", html.unescape(TAG_RE.sub(" ", text))).strip()


def _ts(value):
    """Seconds since 1970 from the many date styles the sources use."""
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            d = datetime.strptime(s, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return int(d.timestamp())
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp())
    except ValueError:
        return 0


def _mention(source, title, text, url, author, ts, engagement=0, extra=""):
    return {
        "source": source,
        "title": strip_html(title)[:300],
        "text": strip_html(text)[:1200],
        "url": url or "",
        "author": author or "",
        "ts": _ts(ts),
        "engagement": int(engagement or 0),
        "where": extra or "",
    }


def _load(raw):
    if isinstance(raw, (dict, list)):
        return raw
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        # GDELT answers in plain text when the search is not allowed (e.g. a word that is too short).
        raise ValueError(raw[:160])


def parse_news(raw):
    data = _load(raw)
    out = []
    for a in data.get("articles", []) or []:
        out.append(_mention("news", a.get("title", ""), "", a.get("url"), a.get("domain"),
                            a.get("seendate"), 0, a.get("sourcecountry", "")))
    return out


def parse_gnews(raw):
    root = ElementTree.fromstring(raw.encode("utf-8") if isinstance(raw, str) else raw)
    out = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        src = item.find("source")
        publisher = src.text if src is not None and src.text else ""
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(" - " + publisher)]
        out.append(_mention("gnews", title, "", item.findtext("link"), publisher, item.findtext("pubDate")))
    return out


def parse_reddit(raw):
    data = _load(raw)
    out = []
    for child in (data.get("data") or {}).get("children", []) or []:
        p = child.get("data") or {}
        if p.get("over_18"):
            continue  # keep the page family friendly
        out.append(_mention("reddit", p.get("title", ""), p.get("selftext", ""),
                            "https://www.reddit.com" + p.get("permalink", ""), "u/" + str(p.get("author", "")),
                            p.get("created_utc"), (p.get("score") or 0) + (p.get("num_comments") or 0),
                            "r/" + str(p.get("subreddit", ""))))
    return out


def parse_hn(raw):
    data = _load(raw)
    out = []
    for h in data.get("hits", []) or []:
        is_comment = bool(h.get("comment_text"))
        title = h.get("title") or ("Re: " + h["story_title"] if h.get("story_title") else "")
        out.append(_mention("hn", "" if is_comment else title, h.get("comment_text") or h.get("story_text") or "",
                            "https://news.ycombinator.com/item?id=%s" % h.get("objectID"), h.get("author"),
                            h.get("created_at_i"), h.get("points") or 0,
                            ("comment on: " + h.get("story_title", "")) if is_comment else "story"))
    return out


def parse_bluesky(raw):
    data = _load(raw)
    out = []
    for p in data.get("posts", []) or []:
        rec = p.get("record") or {}
        handle = (p.get("author") or {}).get("handle", "")
        rkey = (p.get("uri") or "").rsplit("/", 1)[-1]
        url = "https://bsky.app/profile/%s/post/%s" % (handle, rkey) if handle and rkey else ""
        out.append(_mention("bluesky", "", rec.get("text", ""), url, "@" + handle, rec.get("createdAt") or p.get("indexedAt"),
                            (p.get("likeCount") or 0) + (p.get("repostCount") or 0) + (p.get("replyCount") or 0)))
    return out


def parse_mastodon(raw):
    data = _load(raw)
    out = []
    for s in data if isinstance(data, list) else []:
        if s.get("sensitive") or (s.get("language") not in (None, "en")):
            continue
        out.append(_mention("mastodon", "", s.get("content", ""), s.get("url"), "@" + (s.get("account") or {}).get("acct", ""),
                            s.get("created_at"), (s.get("favourites_count") or 0) + (s.get("reblogs_count") or 0)))
    return out


def parse_pasted(raw):
    """Your own text: one mention per line (or per blank-line-separated paragraph for long text)."""
    raw = (raw or "").replace("\r\n", "\n")
    parts = re.split(r"\n\s*\n", raw) if raw.count("\n\n") >= 2 else raw.split("\n")
    now = int(time.time())
    return [_mention("pasted", "", p, "", "", now) for p in parts if len(p.strip()) >= 3][:500]


PARSERS = {"news": parse_news, "gnews": parse_gnews, "reddit": parse_reddit, "hn": parse_hn,
           "bluesky": parse_bluesky, "mastodon": parse_mastodon, "pasted": parse_pasted}


# ---------------------------------------------------------------- scoring

# Words that matter in brand and business news but are missing from (or too mild in) VADER.
# Scale is VADER's: -4 (worst) to +4 (best).
BUSINESS_WORDS = {
    "recall": -2.0, "recalls": -2.0, "recalled": -2.0, "lawsuit": -2.0, "lawsuits": -2.0, "sued": -2.0,
    "sues": -1.8, "outage": -2.2, "outages": -2.2, "layoff": -2.0, "layoffs": -2.0, "breach": -2.2,
    "hacked": -2.4, "leak": -1.5, "leaked": -1.5, "boycott": -2.2, "fined": -2.0, "penalty": -1.6,
    "probe": -1.2, "investigation": -1.2, "downgrade": -1.6, "downgraded": -1.6, "plunge": -1.8,
    "plunges": -1.8, "plunged": -1.8, "slump": -1.8, "slumps": -1.8, "tumble": -1.6, "tumbles": -1.6,
    "overpriced": -2.0, "ripoff": -2.8, "rip-off": -2.8, "buggy": -1.8, "glitch": -1.4, "glitches": -1.4,
    "complaint": -1.8, "complaints": -1.8, "refund": -0.8, "delayed": -1.0, "delays": -1.0,
    "shortage": -1.4, "strike": -1.0, "cancelled": -1.2, "canceled": -1.2, "unreliable": -2.2,
    "laggy": -1.6, "unusable": -2.4, "misleading": -2.0, "greenwashing": -2.0,
    "soar": 2.0, "soars": 2.0, "soared": 2.0, "surge": 1.4, "surges": 1.4, "rally": 1.4, "rallies": 1.4,
    "upgrade": 1.2, "upgraded": 1.4, "partnership": 1.2, "partners": 0.8, "launches": 0.8, "expands": 1.0,
    "expansion": 1.0, "profits": 1.9, "record-breaking": 2.0, "reliable": 2.0, "affordable": 1.5,
    "seamless": 1.8, "intuitive": 1.6, "durable": 1.6, "praised": 2.2, "acclaimed": 2.2, "milestone": 1.4,
}

_analyzer = None


def analyzer():
    global _analyzer
    if _analyzer is None:
        _analyzer = SentimentIntensityAnalyzer()
        _analyzer.lexicon.update(BUSINESS_WORDS)
    return _analyzer


def label_of(compound):
    return "positive" if compound >= 0.05 else "negative" if compound <= -0.05 else "neutral"


def score_text(text):
    """Mood of one piece of text: -1 angry ... +1 happy. Long text is scored sentence by sentence."""
    text = URL_RE.sub(" ", text or "").strip()
    if not text:
        return 0.0
    a = analyzer()
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()][:12]
    if len(sentences) <= 1:
        return a.polarity_scores(text)["compound"]
    scores = [a.polarity_scores(s)["compound"] for s in sentences]
    strong = [s for s in scores if abs(s) >= 0.05] or [0.0]
    return max(-1.0, min(1.0, sum(strong) / len(strong)))


def mentions_name(m, name):
    words = [w for w in re.split(r"\s+", name.lower()) if w]
    hay = (m["title"] + " " + m["text"] + " " + m["url"]).lower()
    if not words:
        return True
    pattern = r"(?<![a-z0-9])" + r"[\s\-_]*".join(re.escape(w) for w in words) + r"(?![a-z0-9])"
    return re.search(pattern, hay) is not None or ("#" + "".join(words)) in hay


def score(mentions, name="", strict=True):
    """Adds a mood (compound, label) to each mention. Drops repeats and, in strict mode, posts that do not
    actually name the brand (news is kept: GDELT already matched the full article)."""
    seen = set()
    out = []
    for m in mentions:
        key = m["url"] or (m["source"] + (m["title"] + m["text"])[:120].lower())
        if key in seen:
            continue
        seen.add(key)
        if strict and name and m["source"] not in ("news", "gnews", "pasted", "mastodon") and not mentions_name(m, name):
            continue
        body = (m["title"] + ". " + m["text"]).strip(". ") if m["title"] and m["text"] else (m["title"] or m["text"])
        c = round(score_text(body[:1500]), 3)
        m = dict(m, compound=c, label=label_of(c))
        out.append(m)
    return out


# ---------------------------------------------------------------- report

STOP = set("""a about above after again against all also am an and any are aren as at be because been before
being below between both but by can cannot could did do does doing don down during each even ever every few
for from further get gets got had has have having he her here hers herself him himself his how i if in into
is isn it its itself just let like ll me more most much my myself new no nor not now of off on once one only
or other our ours out over own re really s same say says said she should so some such t than that the their
theirs them then there these they this those through to too under until up us very via was we well were what
when where which while who whom why will with would you your yours yourself ve d m amp quot via rt https http
www com html still way make made going go know think see want need people time year years day days week
today new news will just get re one two first back many lot thing things honestly actually literally
basically pretty anyone someone everyone something anything everything since yet already""".split())

WORD_RE = re.compile(r"[a-z][a-z'\-]{2,}")


def themes(mentions, name, label, top=8):
    """The words people use most when they praise (label='positive') or complain (label='negative')."""
    brand = set(re.findall(r"[a-z0-9]+", name.lower()))
    counts = Counter()
    for m in mentions:
        if m["label"] != label:
            continue
        words = WORD_RE.findall(URL_RE.sub(" ", (m["title"] + " " + m["text"]).lower()))
        words = [w.strip("'-") for w in words]
        words = [w for w in words if w not in STOP and w not in brand and len(w) > 2 and not w.endswith("'s")]
        grams = set(words)
        grams.update(" ".join(p) for p in zip(words, words[1:]))
        counts.update(grams)
    # Most mentions first; on a tie prefer two-word phrases ("battery life" over "battery").
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -kv[0].count(" "), kv[0]))
    picked = []
    for word, n in ranked[:120]:
        if n < 2:
            break
        parts = set(word.split())
        if any(parts & set(p["word"].split()) and n <= p["mentions"] + 1 for p in picked):
            continue  # skip "battery" when "battery life" is already in, and the other way round
        picked.append({"word": word, "mentions": n})
        if len(picked) == top:
            break
    return picked


def mood_of(compounds):
    """0 (everyone angry) ... 50 (neutral) ... 100 (everyone happy)."""
    if not compounds:
        return None
    return int(round(50 + 50 * sum(compounds) / len(compounds)))


def verdict(mood, n):
    if mood is None or n == 0:
        return "No mentions found"
    if n < 5:
        return "Too few mentions to judge"
    if mood >= 65:
        return "Loved"
    if mood >= 55:
        return "Liked"
    if mood > 45:
        return "Mixed"
    if mood > 35:
        return "Criticised"
    return "Under fire"


def _shares(ms):
    n = len(ms) or 1
    c = Counter(m["label"] for m in ms)
    return {k: round(100.0 * c.get(k, 0) / n, 1) for k in ("positive", "neutral", "negative")}


def report(name, mentions, days=7, statuses=None, now=None):
    now = int(now or time.time())
    days = max(1, int(days))
    start = now - days * 86400
    ms = [m for m in mentions if m["ts"] == 0 or m["ts"] >= start - 3600 or m["source"] == "pasted"]
    ms.sort(key=lambda m: m["ts"], reverse=True)
    comps = [m["compound"] for m in ms]
    shares = _shares(ms)
    mood = mood_of(comps)

    per_source = []
    for key in SOURCES:
        sm = [m for m in ms if m["source"] == key]
        if sm:
            per_source.append(dict(id=key, label=SOURCES[key]["label"], icon=SOURCES[key]["icon"], mentions=len(sm),
                                   mood=mood_of([m["compound"] for m in sm]), **_shares(sm)))

    per_day = []
    buckets = defaultdict(list)
    for m in ms:
        if m["ts"]:
            buckets[datetime.fromtimestamp(m["ts"], timezone.utc).strftime("%Y-%m-%d")].append(m)
    for i in range(days, -1, -1):
        d = datetime.fromtimestamp(now - i * 86400, timezone.utc).strftime("%Y-%m-%d")
        dm = buckets.get(d, [])
        per_day.append({"date": d, "mentions": len(dm), "mood": mood_of([m["compound"] for m in dm]),
                        "negative": sum(1 for m in dm if m["label"] == "negative"),
                        "positive": sum(1 for m in dm if m["label"] == "positive")})

    def rank(m):
        return abs(m["compound"]) + 0.05 * math.log10(1 + m["engagement"])
    praise = sorted([m for m in ms if m["label"] == "positive"], key=rank, reverse=True)[:5]
    critical = sorted([m for m in ms if m["label"] == "negative"], key=rank, reverse=True)[:5]

    # Early warning: are complaints rising in the last 24 hours compared with before?
    alerts = []
    recent = [m for m in ms if m["ts"] >= now - 86400]
    before = [m for m in ms if 0 < m["ts"] < now - 86400]
    if len(recent) >= 5 and len(before) >= 5:
        r, b = _shares(recent)["negative"], _shares(before)["negative"]
        if r - b >= 15:
            alerts.append("Complaints are rising: %d%% of mentions in the last 24 hours are negative, up from %d%% before."
                          % (round(r), round(b)))
        elif b - r >= 15:
            alerts.append("Good news: negative mentions fell to %d%% in the last 24 hours, from %d%% before."
                          % (round(r), round(b)))
    if len(before) >= 3 and len(recent) >= 3 * max(1.0, len(before) / max(1, days - 1)) and len(recent) >= 10:
        alerts.append("Buzz spike: %d mentions in the last 24 hours, about %d times the usual daily amount."
                      % (len(recent), round(len(recent) / max(1.0, len(before) / max(1, days - 1)))))

    return {
        "name": name, "version": VERSION, "generated": now, "days": days,
        "mentions_total": len(ms), "mood": mood, "verdict": verdict(mood, len(ms)),
        "net": None if not ms else round(shares["positive"] - shares["negative"], 1),
        "shares": shares, "per_source": per_source, "per_day": per_day,
        "praise": praise, "critical": critical,
        "praise_themes": themes(ms, name, "positive"), "complaint_themes": themes(ms, name, "negative"),
        "alerts": alerts, "statuses": statuses or {}, "mentions": ms[:400],
    }


def analyze(name, raw_by_source, days=7, strict=True, now=None):
    """raw_by_source: {source_id: raw reply text, or an error message starting with 'ERROR:'}."""
    name = clean_name(name)
    if isinstance(raw_by_source, str):
        raw_by_source = json.loads(raw_by_source)
    mentions, statuses = [], {}
    for key, raw in raw_by_source.items():
        if key not in PARSERS:
            continue
        if isinstance(raw, str) and raw.startswith("ERROR:"):
            statuses[key] = {"ok": False, "found": 0, "note": raw[6:].strip()[:200]}
            continue
        try:
            found = PARSERS[key](raw)
        except Exception as e:  # a source changed its format or sent an error page
            statuses[key] = {"ok": False, "found": 0, "note": "could not read the reply (%s)" % str(e)[:120]}
            continue
        scored = score(found, name, strict)
        statuses[key] = {"ok": True, "found": len(scored), "note": ""}
        mentions.extend(scored)
    return report(name, mentions, days, statuses, now)


def analyze_json(name, raw_json, days=7, strict=True):
    return json.dumps(analyze(name, json.loads(raw_json), int(days), bool(strict)))


def score_json(text):
    c = score_text(text)
    return json.dumps({"compound": round(c, 3), "label": label_of(c)})


# ---------------------------------------------------------------- history (scheduled job)

def history_point(rep):
    return {"date": datetime.fromtimestamp(rep["generated"], timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "mentions": rep["mentions_total"], "mood": rep["mood"], **rep["shares"]}


def add_history(history, rep, keep=240):
    history = list(history or []) + [history_point(rep)]
    return history[-keep:]
