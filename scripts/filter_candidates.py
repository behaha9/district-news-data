#!/usr/bin/env python3
"""Print keyword-matched candidates from inputs/ (pre-fetched by GitHub Actions).

Usage: python3 scripts/filter_candidates.py

Reads inputs/status.json, inputs/candidates.json and inputs/candidates-ptt.json.
Prints the fetch time, then three keyword groups of news candidates and the PTT
list, one line each: published_at | title | source | url  (PTT: push | ... | board).
Exit code 2 when the files are missing or older than MAX_AGE_HOURS, so the caller
knows to fall back to reading the website URLs directly.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

MAX_AGE_HOURS = 6
TAIPEI = timezone(timedelta(hours=8))
GROUPS = [
    ("商圈／老街／商店街", re.compile(r"商圈|老街|商店街")),
    ("活動異動", re.compile(r"延期|取消|停辦|改期|順延|暫停")),
    ("受災影響", re.compile(r"(淹水|積水|停電|受損|停業|暫停營業|招牌).*(商家|店家|攤商|商圈|老街)|(商家|店家|攤商|商圈|老街).*(淹水|積水|停電|受損|停業|暫停營業|招牌)")),
]


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    try:
        status = load("inputs/status.json")
        fetched = datetime.fromisoformat(status["fetched_at"])
    except Exception:
        print("找不到 inputs/status.json，請改用網站網址讀取候選清單。")
        return 2
    age = datetime.now(TAIPEI) - fetched
    print(f"候選清單抓取時間：{status['fetched_at']}（{age.total_seconds() / 3600:.1f} 小時前）")
    stale = age > timedelta(hours=MAX_AGE_HOURS)
    if stale:
        print(f"警告：清單超過 {MAX_AGE_HOURS} 小時，請另外嘗試用網站網址讀取最新清單。")

    if os.path.exists("inputs/candidates.json"):
        items = load("inputs/candidates.json")["items"]
        seen = set()
        for label, pattern in GROUPS:
            rows = [i for i in items if pattern.search(i.get("title", ""))]
            print(f"\n## {label}（{len(rows)} 則）")
            for i in rows:
                mark = "" if i["url"] not in seen else "（同上）"
                seen.add(i["url"])
                print(f"{i.get('published_at','')} | {i.get('title','')} | {i.get('source','')} | {i['url']}{mark}")
    else:
        print("\n找不到 inputs/candidates.json")

    print()
    if os.path.exists("inputs/candidates-ptt.json"):
        posts = load("inputs/candidates-ptt.json")["items"]
        print(f"## PTT（{len(posts)} 篇）")
        for p in posts:
            print(f"{p.get('push_raw', p.get('push',''))} | {p.get('published_at','')} | {p.get('board','')} | {p.get('title','')} | {p.get('url','')}")
    else:
        print("找不到 inputs/candidates-ptt.json")
    return 2 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
