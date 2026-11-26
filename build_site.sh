#!/bin/sh
# Puts the web page, the Python code and the latest scheduled data together in _site/ (what GitHub Pages serves).
set -e
rm -rf _site
mkdir -p _site/py/vaderSentiment _site/data
cp -r web/. _site/
cp monitor.py _site/py/
cp vaderSentiment/__init__.py vaderSentiment/vaderSentiment.py vaderSentiment/vader_lexicon.txt \
   vaderSentiment/emoji_utf8_lexicon.txt _site/py/vaderSentiment/
cp data/*.json _site/data/ 2>/dev/null || true
touch _site/.nojekyll
