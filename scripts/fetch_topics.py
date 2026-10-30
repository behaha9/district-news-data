#!/usr/bin/env python3
"""Collect news for the user's 關注議題 (watch topics) — runs in GitHub Actions.

Usage: python3 scripts/fetch_topics.py

Topics live in reference/topics.json (e.g. 油價, 塑膠袋, 雞蛋), each with a validity window.
For every active topic this asks Google News RSS (zh-TW, last 7 days) for news that mentions
the topic AND shops/vendors, keeps only titles that contain both a topic word and a
merchant word (the user wants the shop-related angle only), turns Google links back into
publisher URLs (same method and cache as fetch_google_news.py) and writes
inputs/topics.json:
  {"updated_at": ..., "topics": [{"id", "name", "since", "until",
     "items": [{"title", "url", "source", "published_at", "resolved"}]}]}
Items are kept 14 days. Old articles re-listed by Google are removed later by
fetch_articles.py (same old-news check as the Google feed). No article text is stored.
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

sys.dont_write_bytecode = True
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import fetch_google_news as g  # noqa: E402

CONFIG = "reference/topics.json"
OUT = "inputs/topics.json"
KEEP_DAYS = 14
# only Taiwan media: .tw domains or well-known Taiwan outlets on other domains
TW_HOSTS = re.compile(r"(\.tw$|^tw\.|(^|\.)(ettoday\.net|setn\.com|nownews\.com|storm\.mg|mirrormedia\.mg|"
                      r"chinatimes\.com|udn\.com|ctwant\.com|cnyes\.com|upmedia\.mg|thenewslens\.com|"
                      r"ctinews\.com|mnews\.tw|newtalk\.tw|today\.line\.me|yam\.com|ftvnews\.com\.tw)$)")
MAX_RESOLVE = 60
BUDGET_SECONDS = 60


def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def main():
    now = datetime.now(g.TAIPEI)
    today = now.strftime("%Y-%m-%d")
    config = load(CONFIG, {}).get("topics", [])
    previous = {t["id"]: t for t in load(OUT, {}).get("topics", [])}
    cache = load(g.CACHE, {})
    g.WHEN = "7d"
    out, todo, log = [], [], []
    for topic in config:
        if not topic.get("active") or topic.get("until", "9999") < today:
            continue
        words = topic["keywords"]
        merchants = topic.get("merchant_words", [])
        topic_re = re.compile("|".join(map(re.escape, words)))
        merchant_re = re.compile("|".join(map(re.escape, merchants))) if merchants else None
        exclude = topic.get("exclude_words", [])
        exclude_re = re.compile("|".join(map(re.escape, exclude))) if exclude else None

        def wanted_title(title):
            return bool(topic_re.search(title)) and (not merchant_re or bool(merchant_re.search(title))) \
                and not (exclude_re and exclude_re.search(title)) and not g.TITLE_DROP.search(title)
        query = "(" + " OR ".join(words) + ")"
        if merchants:
            query += " (" + " OR ".join(merchants[:12]) + ")"
        found = {}
        for old in previous.get(topic["id"], {}).get("items", []):
            if wanted_title(old["title"]):  # re-check kept items when the rules change
                found[g.norm_title(old["title"])] = old
        try:
            rows = g.search(query)
        except Exception as exc:
            log.append(f"{topic['name']} 查詢失敗：{getattr(exc, 'code', type(exc).__name__)}")
            rows = []
        kept = 0
        for row in rows:
            title = row["title"]
            if not wanted_title(title) or row["published_at"][:10] < topic.get("since", "2000"):
                continue
            key = g.norm_title(title)
            if key not in found:
                found[key] = {"title": title, "google_url": row["google_url"], "source": row["source"],
                              "published_at": row["published_at"]}
                kept += 1
        cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
        items = [i for i in found.values() if i["published_at"] >= cutoff]
        for item in items:
            if not item.get("resolved") and item.get("google_url") and item["google_url"] not in cache:
                todo.append(item["google_url"])
        out.append({"id": topic["id"], "name": topic["name"], "since": topic.get("since"),
                    "until": topic.get("until"), "items": items})
        log.append(f"{topic['name']}：Google {len(rows)} 則，新增 {kept} 則，保留 {len(items)} 則")
        time.sleep(1)

    todo = list(dict.fromkeys(todo))[:MAX_RESOLVE]
    pool = ThreadPoolExecutor(g.WORKERS)
    futures = [pool.submit(g.resolve, link) for link in todo]
    try:
        for future in as_completed(futures, timeout=BUDGET_SECONDS):
            link, url, how = future.result()
            cache[link] = {"url": url, "how": how, "at": now.isoformat(timespec="minutes")}
    except Exception:
        pass
    pool.shutdown(wait=False, cancel_futures=True)

    for topic in out:
        for item in topic["items"]:
            hit = (cache.get(item.get("google_url")) or {}).get("url")
            if hit:
                item["url"], item["resolved"] = hit, True
            else:
                item.setdefault("url", item.get("google_url"))
                item.setdefault("resolved", False)
        from urllib.parse import urlsplit
        topic["items"] = [i for i in topic["items"]
                          if not i["resolved"] or TW_HOSTS.search(urlsplit(i["url"]).hostname or "")]
        topic["items"].sort(key=lambda i: i["published_at"], reverse=True)

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"updated_at": now.isoformat(timespec="seconds"), "topics": out}, fh,
                  ensure_ascii=False, indent=1)
    with open(g.CACHE, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, separators=(",", ":"))
    print("關注議題：" + ("；".join(log) if log else "沒有進行中的議題"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
