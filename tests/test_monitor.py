import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import monitor  # noqa: E402

NOW = int(time.time())


def sample_raw():
    return {
        "news": json.dumps({"articles": [
            {"title": "Acme Motors recalls 50,000 cars over brake fault", "url": "https://n.example/1", "domain": "n.example",
             "seendate": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(NOW - 3600)), "sourcecountry": "US"},
            {"title": "Acme Motors wins award for best family car", "url": "https://n.example/2", "domain": "n.example",
             "seendate": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(NOW - 2 * 86400))},
        ]}),
        "reddit": json.dumps({"data": {"children": [
            {"data": {"title": "My Acme Motors car is great, love it", "selftext": "Smooth and reliable.", "permalink": "/r/cars/1",
                      "author": "a", "created_utc": NOW - 5000, "score": 10, "num_comments": 3, "subreddit": "cars"}},
            {"data": {"title": "Unrelated post about bikes", "selftext": "", "permalink": "/r/bikes/2",
                      "author": "b", "created_utc": NOW - 5000, "score": 1, "num_comments": 0, "subreddit": "bikes"}},
        ]}}),
        "hn": json.dumps({"hits": [
            {"comment_text": "Acme Motors support is terrible, worst service ever", "objectID": "9", "author": "c",
             "created_at_i": NOW - 7200, "story_title": "Car makers"}]}),
        "bluesky": "ERROR: the site answered 403 Forbidden",
        "mastodon": "<html>not json</html>",
    }


def test_plan_has_links():
    links = monitor.plan("Acme Motors", 7)
    ids = [s["id"] for s in links]
    assert "news" in ids and "reddit" in ids and "gnews" not in ids
    assert "%22Acme%20Motors%22" in links[0]["url"]
    assert "gnews" in [s["id"] for s in monitor.plan("Acme", 7, "server")]


def test_scores_and_business_words():
    assert monitor.score_text("I love this phone, it is amazing") > 0.5
    assert monitor.score_text("Acme announces recall after outage") < -0.3
    assert monitor.label_of(0.0) == "neutral"


def test_analyze_end_to_end():
    rep = monitor.analyze("Acme Motors", sample_raw(), days=7, now=NOW)
    st = rep["statuses"]
    assert st["news"]["found"] == 2 and st["reddit"]["found"] == 1  # unrelated post dropped
    assert st["bluesky"]["ok"] is False and "403" in st["bluesky"]["note"]
    assert st["mastodon"]["ok"] is False
    assert rep["mentions_total"] == 4
    assert 0 <= rep["mood"] <= 100
    assert rep["critical"][0]["source"] in ("hn", "news")
    assert len(rep["per_day"]) == 8
    assert sum(d["mentions"] for d in rep["per_day"]) == 4
    json.dumps(rep)


def test_pasted_and_gnews():
    ms = monitor.parse_pasted("Great service!\nTerrible food\n\n")
    assert len(ms) == 2
    rss = ("<rss><channel><item><title>Acme soars on profits - Daily News</title><link>https://g/1</link>"
           "<pubDate>Tue, 24 Nov 2026 10:00:00 GMT</pubDate><source url='x'>Daily News</source></item></channel></rss>")
    g = monitor.parse_gnews(rss)
    assert g[0]["title"] == "Acme soars on profits" and g[0]["author"] == "Daily News" and g[0]["ts"] > 0


def test_name_match():
    m = {"title": "Loving my new #acmemotors ride", "text": "", "url": ""}
    assert monitor.mentions_name(m, "Acme Motors")
    assert not monitor.mentions_name({"title": "acmeish", "text": "", "url": ""}, "Acme")


def test_history():
    rep = monitor.analyze("Acme Motors", sample_raw(), now=NOW)
    h = monitor.add_history([{"mood": 50}] * 300, rep)
    assert len(h) == 240 and h[-1]["mentions"] == 4
