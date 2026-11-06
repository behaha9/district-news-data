#!/usr/bin/env python3
"""Save the website's homepage news list (近 7 天) to inputs/homepage.json (runs in GitHub Actions).

Usage: python3 scripts/fetch_homepage.py

Source: GET /api/public/v1/homepage-articles (v58a, Sites62). Every article on the
homepage list, including reports merged into the same event (primary: false).
The AI reviews each of these (CURATION.md 四之二) so the website can tell
"AI 隱藏" apart from "AI 還沒看".

Kept per article: url, title, source, published_at (Taipei), event_key, primary, and,
when the API's presentation block has it, the site's own short summary, cities and
districts (to help the reviewer; not shown anywhere).
The file is only replaced after a successful fetch; on failure the old file stays and
review.py reports it as stale.
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

TAIPEI = timezone(timedelta(hours=8))
URL = "https://business-district-monitor.behaha.chatgpt.site/api/public/v1/homepage-articles"
OUT = "inputs/homepage.json"
ARCHIVE = "inputs/homepage-archive.json"
UA = "district-news-data-fetcher/1.0 (+https://github.com/behaha9/district-news-data)"
ATTEMPTS = 4
WAIT = 20


def to_taipei(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TAIPEI).isoformat(timespec="seconds")


def fetch():
    last = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urlopen(Request(URL, headers={"User-Agent": UA}), timeout=40) as resp:
                data = json.load(resp)
            if isinstance(data.get("items"), list):
                return data
            last = "回應沒有 items 陣列"
        except Exception as exc:  # network / JSON errors: retry
            last = f"{type(exc).__name__}: {exc}"
        print(f"首頁清單第 {attempt} 次失敗：{last}", file=sys.stderr)
        if attempt < ATTEMPTS:
            time.sleep(WAIT)
    return None


def main():
    data = fetch()
    if data is None:
        print("首頁清單：抓取失敗，保留舊檔")
        return 0
    impact = (data.get("presentation") or {}).get("commerceImpact") or []
    items = data["items"]
    # commerceImpact lists one entry per article in the same order (publicId = event_key);
    # use the position only when it lines up, otherwise fall back to the first entry per event.
    aligned = len(impact) == len(items) and all(
        c.get("publicId") == it.get("event_key") for c, it in zip(impact, items))
    by_event = {}
    for c in impact:
        by_event.setdefault(c.get("publicId"), c)
    out = []
    for n, it in enumerate(items):
        if not str(it.get("url", "")).startswith("http"):
            continue
        row = {
            "url": it["url"],
            "title": it.get("title", ""),
            "source": it.get("source", ""),
            "published_at": to_taipei(it["published_at"]) if it.get("published_at") else "",
            "event_key": it.get("event_key", ""),
            "primary": bool(it.get("primary")),
        }
        c = impact[n] if aligned else by_event.get(it.get("event_key"), {})
        if c.get("summary"):
            row["summary"] = c["summary"][:160]
        if c.get("cities"):
            row["cities"] = c["cities"]
        if c.get("districts"):
            row["districts"] = c["districts"]
        if c.get("dateOnly"):
            row["date_only"] = True
        out.append(row)
    doc = {
        "fetched_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "generated_at": to_taipei(data["generated_at"]) if data.get("generated_at") else "",
        "window": data.get("window", "7d"),
        "event_count": data.get("event_count"),
        "article_count": len(out),
        "items": out,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    print(f"首頁清單：{doc['event_count']} 件事件／{len(out)} 篇")
    total, added = update_archive(out, doc["fetched_at"])
    print(f"7 天累積清單：{total} 篇（本次新增 {added}）")
    return 0


def window_start(now):
    """首頁「近 7 天」＝今天與前六個臺北日期（與網站 homeDateGroups 相同）。"""
    return (now.astimezone(TAIPEI).date() - timedelta(days=6)).isoformat()


def update_archive(items, fetched_at, path=ARCHIVE):
    """把這次首頁清單併進 7 天累積清單（v58c）。

    網站首頁清單只暫存在各機房快取，文章會消失又出現；累積清單記下 7 個臺北日期內
    曾出現在首頁的每一篇，給 AI 審查（review.py）與網站補齊首頁用。
    已在清單的文章更新欄位與 last_seen_at；超出 7 個臺北日期的移除。
    """
    try:
        with open(path, encoding="utf-8") as fh:
            archive = json.load(fh)
    except (FileNotFoundError, ValueError):
        archive = {}
    by_url = {a["url"]: a for a in archive.get("items", []) if a.get("url")}
    added = 0
    for it in items:
        old = by_url.get(it["url"])
        row = dict(it)
        row["first_seen_at"] = old.get("first_seen_at", fetched_at) if old else fetched_at
        row["last_seen_at"] = fetched_at
        if not old:
            added += 1
        by_url[it["url"]] = row
    start = window_start(datetime.fromisoformat(fetched_at))
    keep = [a for a in by_url.values() if str(a.get("published_at", ""))[:10] >= start]
    keep.sort(key=lambda a: (a.get("published_at", ""), a["url"]), reverse=True)
    doc = {
        "schema_version": 1,
        "updated_at": fetched_at,
        "window_start": start,
        "article_count": len(keep),
        "items": keep,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    return len(keep), added


if __name__ == "__main__":
    sys.exit(main())
