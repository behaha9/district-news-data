#!/usr/bin/env python3
"""Print keyword-matched candidates from inputs/ (pre-fetched by GitHub Actions).

Usage: python3 scripts/filter_candidates.py

Reads inputs/status.json, inputs/candidates.json .
Prints the fetch time, then three keyword groups of news candidates, the priority-district
search results (group 4) and Google News items not already listed (group 5).
list, one line each: published_at | title | source | url .
Exit code 2 when the files are missing or older than MAX_AGE_HOURS, so the caller
knows to fall back to reading the website URLs directly.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.dont_write_bytecode = True  # 不在 scripts/ 產生 __pycache__
import priority  # noqa: E402

MAX_AGE_HOURS = 6
TAIPEI = timezone(timedelta(hours=8))
GROUPS = [
    ("商圈／老街／商店街", re.compile(r"商圈|老街|商店街")),
    ("活動異動", re.compile(r"延期|取消|停辦|改期|順延|暫停")),
    ("受災影響", re.compile(r"(淹水|積水|停電|受損|停業|暫停營業|招牌).*(商家|店家|攤商|商圈|老街)|(商家|店家|攤商|商圈|老街).*(淹水|積水|停電|受損|停業|暫停營業|招牌)")),
]


def taipei_time(item, article=None):
    """Time to copy into published_at. The publisher's own time (prefetched article) wins.
    Bing times are unreliable (often hours off), so the website marks them date_only; then use
    that date at 00:00, which the site shows as date only."""
    if article and article.get("published_at"):
        try:
            value = datetime.fromisoformat(article["published_at"]).astimezone(TAIPEI)
            return value.isoformat(timespec="seconds") + "（原文時間）"
        except Exception:
            pass
    if item.get("date_only") and item.get("published_date"):
        return f"{item['published_date']}T00:00:00+08:00（只有日期）"
    try:
        value = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        return value.astimezone(TAIPEI).isoformat(timespec="minutes")
    except Exception:
        return item.get("published_at", "")


def article_line(art, hint):
    """One line of prefetched facts: site, publisher summary, cities and districts in the body."""
    if not (art.get("description") or art.get("cities") or art.get("published_at")):
        return "↳ 原文是動態網頁，抓不到內容，請依標題判斷（必要時再打開原文）"
    cities = "、".join(f"{c}{n}" for c, n in (art.get("cities") or {}).items()) or "（未提到縣市）"
    parts = [f"↳ 原文：{art.get('site_name') or '站名不明'}"]
    hint = art.get("date_hint")
    if hint and hint < (datetime.now(TAIPEI) - timedelta(days=7)).strftime("%Y-%m-%d"):
        parts.insert(0, f"⚠ 原文日期是 {hint}（依{art.get('date_hint_from')}），疑似舊聞被重新列出，不可收錄")
    if art.get("description"):
        parts.append(f"摘要：{art['description'][:90]}")
    parts.append(f"內文縣市：{cities}")
    if art.get("districts"):
        parts.append("內文重點商圈：" + "、".join(art["districts"]))
    top = next(iter(art.get("cities") or {}), None)
    if hint and top and all(c != top for _, c in hint):
        parts.append(f"⚠ 名單縣市（{'、'.join(c for _, c in hint)}）與內文最常出現的縣市（{top}）不同，請勿直接標★")
    return "｜".join(parts)


def url_key(url):
    return re.sub(r"^https?://(www\.)?", "", (url or "").split("#")[0].rstrip("/"))


def norm_title(title):
    return re.sub(r"[\s\W_]+", "", title or "")[:40]


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
    print("【判斷提醒】縣市或商圈判斷不出來「不是」排除理由：縣市留空、priority 設 false 照收。"
          "議員批評但內容是具體商圈影響（如工程、工安衝擊店家）要收。標題有商圈的每一則都要判斷。"
          "「★可能是重點商圈」只是名稱比對，標 priority 前要打開原文確認縣市與名單相同，縣市不可從名單反推。「活動異動」組只收商圈、老街、商店街的活動；縣市節慶、一般市集、演唱會、頒獎典禮、運動會、企業與選舉活動延期都不收。\n")
    age = datetime.now(TAIPEI) - fetched
    print(f"候選清單抓取時間：{status['fetched_at']}（{age.total_seconds() / 3600:.1f} 小時前）")
    stale = age > timedelta(hours=MAX_AGE_HOURS) or "candidates.json" not in status.get("files", {})
    if stale:
        print(f"警告：清單超過 {MAX_AGE_HOURS} 小時或本次抓取失敗（見 inputs/fetch-log.txt），請另外嘗試用網站網址讀取最新清單。")

    if os.path.exists("inputs/candidates.json"):
        items = load("inputs/candidates.json")["items"]
        patterns = priority.load()
        try:
            articles = load("inputs/articles.json").get("articles", {})
        except Exception:
            articles = {}
        seen = set()
        for label, pattern in GROUPS:
            rows, group_urls = [], set()
            for i in items:
                if pattern.search(i.get("title", "")) and i["url"] not in group_urls:
                    group_urls.add(i["url"])
                    rows.append(i)
            print(f"\n## {label}（{len(rows)} 則）")
            for i in rows:
                if i["url"] in seen:
                    print(f"（已列於前組）{i.get('title','')}")
                    continue
                seen.add(i["url"])
                hint = priority.hints(i.get("title", ""), patterns)
                mark = "【★可能是重點商圈：" + "、".join(f"{n}（{c}）" for n, c in hint) + "】" if hint else ""
                art = articles.get(i["url"])
                ok = art and art.get("status") == 200
                print(f"{taipei_time(i, art if ok else None)} | {i.get('title','')}{mark} | {i.get('source','')} | {i['url']}")
                if ok:
                    print("    " + article_line(art, hint))
                elif art:
                    print(f"    ↳ 原文讀取失敗（{art.get('status')}），請依標題判斷或另開原文")
        # 第 4 組：GitHub 每天用 Bing 新聞逐一查 115 年重點商圈（取代網路搜尋輪查）
        try:
            district_items = load("inputs/district_news.json").get("items", [])
        except Exception:
            district_items = []
        rows = [d for d in district_items if d["url"] not in seen]
        print(f"\n## 重點商圈新聞（每日逐一查詢，{len(rows)} 則，未列於前三組）")
        for d in rows:
            seen.add(d["url"])
            art = articles.get(d["url"])
            ok = art and art.get("status") == 200
            label = f"【重點商圈：{d['district']}（{d['city']}）】"
            print(f"{taipei_time(d, art if ok else None)} | {d['title']}{label} | {d.get('source','')} | {d['url']}")
            if ok:
                print("    " + article_line(art, [(d["district"], d["city"])]))
            elif art:
                print(f"    ↳ 原文讀取失敗（{art.get('status')}），請依標題判斷或另開原文")
        # 第 5 組：Google 新聞補充（Bing 收錄落後時的第二來源，已去除前四組出現過的新聞）
        try:
            google_items = load("inputs/google_news.json").get("items", [])
        except Exception:
            google_items = []
        seen_keys = {url_key(u) for u in seen}
        seen_titles = {norm_title(i.get("title", "")) for i in items + district_items}
        rows = [g for g in google_items
                if url_key(g["url"]) not in seen_keys and norm_title(g["title"]) not in seen_titles]
        print(f"\n## Google 新聞補充（{len(rows)} 則，未列於前四組）")
        for g in rows:
            hint = priority.hints(g["title"], patterns)
            mark = "【★可能是重點商圈：" + "、".join(f"{n}（{c}）" for n, c in hint) + "】" if hint else ""
            if not g.get("resolved"):
                print(f"{taipei_time(g)} | {g['title']}{mark} | {g.get('source','')} | （Google 轉址，未還原原媒體網址）")
                print("    ↳ 不可直接用 Google 連結收錄；要收錄請用網頁搜尋標題，找到原媒體網址再填 url")
                continue
            art = articles.get(g["url"])
            ok = art and art.get("status") == 200
            print(f"{taipei_time(g, art if ok else None)} | {g['title']}{mark} | {g.get('source','')} | {g['url']}")
            if ok:
                print("    " + article_line(art, hint))
            elif art:
                print(f"    ↳ 原文讀取失敗（{art.get('status')}），請依標題判斷或另開原文")
    else:
        print("\n找不到 inputs/candidates.json")

    return 2 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
