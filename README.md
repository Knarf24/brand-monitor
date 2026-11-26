# 📣 Brand Reputation Monitor

**Live app: https://knarf24.github.io/brand-monitor/**

Small businesses rarely know what people say about them online, and checking every news site and forum by
hand takes hours. Type a brand, product or name and this app gathers what **news sites (GDELT), Reddit,
Hacker News, Bluesky and Mastodon** said about it, **scores every mention by mood** and shows:

- a **mood score** from 0 to 100 (50 = neutral) and the share of positive, neutral and negative mentions
- mood **per source** and **per day**
- what people **praise** and what they **complain about** (most used words and phrases)
- the most positive and most negative mentions, with links
- an **early warning** when complaints rise in the last 24 hours, or mentions suddenly spike
- a CSV download of every mention
- **Score your own text**: paste reviews or posts from places the app can't read (X, Facebook, Google reviews)

A scheduled GitHub Actions job checks the names in [`watchlist.json`](watchlist.json) twice a day (adding
Google News) and keeps their history in [`data/`](data), so the page can show how the mood changes over weeks.

## Python everywhere

| Where | What runs |
|---|---|
| Web page | `monitor.py` + VADER in the browser with [Pyodide](https://pyodide.org) (`web/`) |
| Scheduled job | `python collect.py --watchlist` (`.github/workflows/pages.yml`) |
| Your computer | `python collect.py "Spotify" --days 7` prints a report (no installs needed) |

`monitor.py` builds the search links, reads each source's reply into one "mention" format, scores each mention
with [VADER](https://github.com/cjhutto/vaderSentiment) (a sentiment model made for social media text, bundled
in `vaderSentiment/`, MIT) plus business words it doesn't know (recall, lawsuit, outage, layoffs, soars,
award…), and sums it all up.

Combines three GeeksforGeeks Python projects: Twitter Sentiment Analysis, Scraping Reddit and Scrape Most
Reviewed News.

## Honest notes

- **X (Twitter) is not included**: its API is paid. Bluesky and Mastodon are free alternatives, and you can
  paste X posts into "Score your own text".
- Google News has no "allow other websites" header, so only the scheduled job reads it.
- Free services may limit how often they are asked. If one fails, the rest still count.
- Mood scoring is English only, news is scored on the headline, and sarcasm fools it. Use it as a quick pulse.

## Run the tests

```
pip install pytest
python -m pytest -q tests
```
