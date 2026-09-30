#!/bin/sh
# Builds dist/ask-your-financials/ from src/ and the data built by tools/site_data.py.
set -eu
cd "$(dirname "$0")"
OUT=dist/ask-your-financials
mkdir -p "$OUT"
cp src/index.html src/style.css src/app.js src/data.json src/transcript.css "$OUT/"
mkdir -p "$OUT/transcripts" && cp src/transcripts/*.html "$OUT/transcripts/"
FAV=../../the-ai-pipe-website/website/public
if [ -d "$FAV" ]; then cp "$FAV/favicon.svg" "$FAV/favicon.png" "$FAV/apple-touch-icon.png" "$OUT/"; fi
ls "$OUT"
