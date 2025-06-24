#!/usr/bin/env python3
"""
advanced_csfloat_watcher.py
———————————————
Poll CSFloat listings, compute discounts versus
(1) CSFloat's ML predicted_price  and
(2) the median of recent real sales,
and print bargains that beat user-supplied thresholds.

Dependencies:  requests  python-dateutil
    pip install requests python-dateutil
"""

from __future__ import annotations
import argparse, concurrent.futures as futures, datetime as dt
import functools, json, os, sys, time, urllib.parse
from collections import OrderedDict
from statistics import median
from typing import Any, Dict, List, Optional

import requests
from dateutil import parser as dparse

# ───────────────────────── configurable constants
BASE          = "https://csfloat.com/api/v1"
LIST_URL      = f"{BASE}/listings"
HIST_URL_TMPL = f"{BASE}/history/{{}}/sales?paint_index={{}}"
TIMEOUT       = 15       # seconds for HTTP
RETRY_SLEEP   = 10       # after 429/5xx
POOL_WORKERS  = 1
HIST_TTL      = dt.timedelta(minutes=10)
HIST_MAX      = 4096     # LRU cache size

# ───────────────────────── tiny LRU-TTL cache for trade history
class _HistCache(OrderedDict):
    def __init__(self, ttl: dt.timedelta, maxlen: int):
        super().__init__()
        self.ttl, self.maxlen = ttl, maxlen

    def get(self, key: str) -> Optional[List[int]]:
        try:
            prices, ts = super().pop(key)
            if dt.datetime.utcnow() - ts < self.ttl:
                super().__setitem__(key, (prices, ts))  # refresh LRU order
                return prices
        except KeyError:
            pass
        return None

    def put(self, key: str, prices: List[int]) -> None:
        if key in self:
            super().pop(key)
        elif len(self) >= self.maxlen:
            self.popitem(last=False)
        super().__setitem__(key, (prices, dt.datetime.utcnow()))

hist_cache = _HistCache(HIST_TTL, HIST_MAX)

# ───────────────────────── helpers
def read_key(file_path: str) -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass
    env = os.getenv("CSFLOAT_API_KEY")
    if env:
        return env.strip()
    sys.exit(f"[fatal] no API key found (file {file_path!r} missing and "
             "CSFLOAT_API_KEY not set)")

def _request(method: str, url: str, key: str, **kwargs) -> Any:
    headers = {"Authorization": key}
    kwargs.setdefault("timeout", TIMEOUT)
    while True:
        try:
            resp = requests.request(method, url, headers=headers, **kwargs)
        except requests.RequestException as e:
            print(f"[net] {e} – retrying in {RETRY_SLEEP}s", file=sys.stderr)
            time.sleep(RETRY_SLEEP); continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 503):
            print(f"[rate-limit] {url} => {resp.status_code}; sleep {RETRY_SLEEP}s",
                  file=sys.stderr)
            time.sleep(RETRY_SLEEP); continue
        print(f"[warn] HTTP {resp.status_code} for {url}: {resp.text[:120]}",
              file=sys.stderr)
        return None

def fetch_list(sort_by: str, limit: int, key: str) -> List[Dict[str, Any]]:
    params = {"limit": limit, "page": 0, "sort_by": sort_by, "min_price": 100, "type": "buy_now"}
    data = _request("GET", LIST_URL, key, params=params)["data"]
    return data or []

def fetch_hist(name: str, paint_index: int, key: str) -> List[int]:
    cache_key = f"{name}"
    if paint_index:
        cache_key += "|{paint_index}"
    if (cached := hist_cache.get(cache_key)) is not None:
        return cached
    encoded = urllib.parse.quote(name, safe="")
    url = HIST_URL_TMPL.format(encoded, paint_index)
    raw = _request("GET", url, key)
    prices: List[int] = []
    if isinstance(raw, list):
        prices = [int(sale["price"])
                  for sale in raw
                  if sale.get("price") is not None]
    hist_cache.put(cache_key, raw)
    return raw

def ml_discount(lst: Dict[str, Any]) -> Optional[float]:
    ref = lst.get("reference")
    if not ref or not ref.get("predicted_price"):
        return None
    pp, price = ref["predicted_price"], lst["price"]
    return 100 * (pp - price) / pp

def trade_discount(lst: Dict[str, Any], prices: List[int]) -> Optional[float]:
    if not prices:
        return None
    med = median(prices)
    if med <= 0:
        return None
    return 100 * (med - lst["price"]) / med

def fmt(lst: Dict[str, Any], mld: Optional[float],
        tdd: Optional[float]) -> str:
    stamp = dparse.isoparse(lst["created_at"]).strftime("%m-%d %H:%M")
    price = lst["price"] / 100
    name  = lst["item"]["market_hash_name"]
    ml_s  = f"{mld:5.1f}%" if mld is not None else "  –  "
    td_s  = f"{tdd:5.1f}%" if tdd is not None else "  –  "
    link  = f"https://csfloat.com/item/{lst['id']}"
    return f"{ml_s} | {td_s} | ${price:9,.2f} | {name} | {link} | {stamp}"

# ───────────────────────── main loop
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Advanced CSFloat discount watcher (ML + real sales).")
    ap.add_argument("--key-file", default="key.txt",
                    help="text file containing your CSFloat API key "
                         "(default: key.txt)")
    ap.add_argument("--interval", type=int, default=60,
                    help="poll interval in seconds (default 60)")
    ap.add_argument("--limit", type=int, default=50,
                    help="how many newest listings to fetch (≤ 50)")
    ap.add_argument("--sort", choices=("most_recent", "highest_discount"),
                    default="most_recent", help="initial market sort key")
    ap.add_argument("--min-ml-discount", type=float, default=0.0,
                    help="print when ML-discount ≥ this (0 = ignore)")
    ap.add_argument("--min-trade-discount", type=float, default=0.0,
                    help="print when trade-discount ≥ this (0 = ignore)")
    ap.add_argument("--history-days", type=int, default=7,
                    help="consider sales within N days (median) (default 7)")
    ap.add_argument("--history-limit", type=int, default=40,
                    help="max sales to request per item (default 40)")
    ap.add_argument("--once", action="store_true",
                    help="run one cycle then exit")
    args = ap.parse_args()

    key = read_key(args.key_file)
    print(f"[info] every {args.interval}s  limit={args.limit}  "
          f"sort={args.sort}  ml≥{args.min_ml_discount}%  "
          f"trade≥{args.min_trade_discount}%  hist={args.history_days}d",
          file=sys.stderr)

    # filter helper
    def qualifies(mld: Optional[float], tdd: Optional[float]) -> bool:
        ok_ml  = (args.min_ml_discount   <= 0) or (mld is not None and
                                                  mld   >= args.min_ml_discount)
        ok_trd = (args.min_trade_discount <= 0) or (tdd is not None and
                                                   tdd   >= args.min_trade_discount)
        # require at least one condition to pass
        return ok_ml or ok_trd

    while True:
        listings = [l for l in fetch_list(args.sort, args.limit, key) if l["item"].get("paint_index", False)]
        # collect unique item keys for history fetch
        hist_keys = {(l["item"]["market_hash_name"], l["item"]["paint_index"])
                     for l in listings}
        # pull sales histories in parallel
        with futures.ThreadPoolExecutor(max_workers=POOL_WORKERS) as pool:
            hist_map = {hkey: pool.submit(fetch_hist, hkey[0], hkey[1], key=key)
                        for hkey in hist_keys}
        # now process listings
        now = dt.datetime.utcnow() - dt.timedelta(days=args.history_days)
        def recent(prices: List[int], dates: List[str]) -> List[int]:
            return [p for p, d in zip(prices, dates)
                    if dparse.isoparse(d) >= now]

        for lst in listings:
            name, pidx = lst["item"]["market_hash_name"], lst["item"]["paint_index"]
            hist_json = hist_map[(name, pidx)].result()
            # 'hist_json' holds raw sale dicts; filter last N days
            if hist_json:
                recent_sales = [s for s in hist_json
                                if dparse.isoparse(s["created_at"]) >= now]
                sale_prices = [int(s["unit_price"]) for s in recent_sales][:args.history_limit]
            else:
                sale_prices = []
            mld = ml_discount(lst)
            tdd = trade_discount(lst, sale_prices) if sale_prices else None
            if qualifies(mld, tdd):
                print(fmt(lst, mld, tdd))
            print('-'*40)

        if args.once:
            break
        time.sleep(args.interval)

if __name__ == "__main__":
    main()

