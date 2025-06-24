# CSFloat Discount Watcher

Polls the CSFloat market and prints listings that have a significant discount based on:
- CSFloat's ML-predicted price
- Recent real sales (via CSFloat's internal API)

No Steam tax, no scraping, no paid APIs.

## Features

- Computes **ML discount** and **trade discount** per item
- Filters listings by user-defined thresholds
- Lightweight: all JSON endpoints
- Auto-caches trade history to reduce API load

## Setup

```bash
# clone the repo
git clone https://github.com/timakrutoi/csfloat_fetcher
cd csfloat_fetcher

# create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# install dependencies
pip install -r requirements.txt
```

# API Key

Put your CSFloat API key in a file named `key.txt` (one line, no quotes).
Alternatively, set the `CSFLOAT_API_KEY` environment variable.

Get a key at https://csfloat.com/developer
# Usage

```bash
python3 refresher.py \
    --min-ml-discount 7 \
    --min-trade-discount 10 \
    --interval 90
```

Use `--once` to run a single cycle.
