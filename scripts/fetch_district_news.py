#!/usr/bin/env python3
"""Search Bing News for each 115年重點商圈 (runs in GitHub Actions).

Usage: python3 scripts/fetch_district_news.py

Replaces the curator's web-search rotation: every run queries the next SLICE
priority districts (exact name, newest first) through Bing News RSS, keeps items
from the last KEEP_DAYS days whose title or description really mentions the
district, and stores them in inputs/district_news.json. With hourly and
on-demand runs, every district is searched about twice a day.

Bing's times are unreliable (often hours off), so items are marked date_only;
scripts/fetch_articles.py later adds the publisher's own time when it can.
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(__file__))
import priority  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))
OUT = "inputs/district_news.json"
REF = os.path.join(os.path.dirname(__file__), "..", "reference", "priority-districts-115.json")
SLICE = int(os.environ.get("DISTRICT_SLICE") or 40)
WORKERS = 4
TIMEOUT = 15
KEEP_DAYS = 7
MAX_ITEMS = 600
UA = ("Mozilla/5.0 (compatible; district-news-data-fetcher/1.0; "
      "+https://github.com/behaha9/district-news-data)")
TAG = re.compile(r"<[^>]+>")


def districts():
    with open(REF, encoding="utf-8") as fh:
        rows = json.load(fh)["districts"]
    return [r for r in rows if "夜市" not in r["name"]]


def query_name(name):
    return name.split()[-1]


def keys_for(row):
    name = query_name(row["name"])
    keys = {name, row["name"], *row.get("aliases", [])}
    stem = priority._stem(row["name"])
    if len(stem) >= 3:
        keys.add(stem)
    return [k for k in keys if k]


def publisher_url(link):
    try:
        parsed = urlparse(link)
        if parsed.hostname and parsed.hostname.endswith("bing.com"):
            target = parse_qs(parsed.query).get("url", [""])[0]
            return target if target.startswith("https://") else None
        return link if link.startswith("https://") else None
    except Exception:
        return None


def search(row):
    q = f'"{query_name(row["name"])}"'
    url = "https://www.bing.com/news/search?" + urlencode(
        {"q": q, "format": "rss", "setlang": "zh-tw", "cc": "tw", "qft": 'sortbydate="1"'})
    found = []
    try:
        with urlopen(Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT) as resp:
            root = ET.fromstring(resp.read(2_000_000))
    except Exception as exc:
        return row, None, str(exc)[:80]
    cutoff = datetime.now(TAIPEI) - timedelta(days=KEEP_DAYS + 1)
    keys = keys_for(row)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc = TAG.sub("", item.findtext("description") or "").strip()
        link = publisher_url((item.findtext("link") or "").strip())
        source = ""
        for child in item:
            if child.tag.lower().endswith("source") and child.text:
                source = child.text.strip()
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(TAIPEI)
        except Exception:
            continue
        if not link or when < cutoff or "夜市" in title:
            continue
        where = "title" if any(k in title for k in keys) else "description" if any(k in desc for k in keys) else None
        if not where:
            continue
        found.append({
            "title": title, "url": link, "source": source,
            "published_at": when.isoformat(timespec="minutes"),
            "published_date": when.date().isoformat(), "date_only": True,
            "district": row["name"], "city": row["city"], "matched_in": where,
        })
    return row, found, None


def main():
    rows = districts()
    try:
        with open(OUT, encoding="utf-8") as fh:
            state = json.load(fh)
    except Exception:
        state = {"position": 0, "items": []}
    start = state.get("position", 0) % len(rows)
    batch = [rows[(start + i) % len(rows)] for i in range(SLICE)]
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(search, batch))
    errors = [(r["name"], e) for r, _, e in results if e]
    items = {i["url"]: i for i in state.get("items", [])}
    added = 0
    for _, found, _ in results:
        for i in found or []:
            if i["url"] not in items:
                items[i["url"]] = i
                added += 1
    cutoff = (datetime.now(TAIPEI) - timedelta(days=KEEP_DAYS)).date().isoformat()
    kept = sorted((i for i in items.values() if i["published_date"] >= cutoff),
                  key=lambda i: i["published_at"], reverse=True)[:MAX_ITEMS]
    state = {
        "fetched_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "position": (start + SLICE) % len(rows),
        "last_batch": [r["name"] for r in batch],
        "errors": errors[:20],
        "items": kept,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    print(f"重點商圈查詢：第 {start + 1}–{start + SLICE} 處（共 {len(rows)} 處），新增 {added} 則，"
          f"失敗 {len(errors)} 處；目前保留 {len(kept)} 則")
    return 0


if __name__ == "__main__":
    sys.exit(main())
