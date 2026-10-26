#!/usr/bin/env python3
"""Search Google News RSS for 商圈 news (runs in GitHub Actions) — second source next to Bing.

Usage: python3 scripts/fetch_google_news.py

Why: from 10/24 evening to 10/26 Bing News returned no new 「商圈」 articles at all, while
Google News listed dozens. This script asks Google News RSS (zh-TW, last 2 days) a few
keyword queries, keeps items whose TITLE mentions 商圈／老街／商店街 or an activity change
or damage word, and turns each Google redirect link back into the publisher's own URL so
that article prefetch, thumbnails and de-duplication work the same as for other sources.

Writes inputs/google_news.json:
  {"fetched_at": ..., "stats": {...},
   "items": [{"title", "url", "google_url", "source", "source_site", "published_at",
              "query", "resolved"}]}
"url" is the publisher URL when resolved; otherwise it stays the Google link and
"resolved" is false (curators must then find the publisher URL themselves).
Resolutions are cached in inputs/google_resolve.json (8 days) so each link is resolved once.
Only titles, links, source names and times are stored — no article text.
"""
import base64
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True

TAIPEI = timezone(timedelta(hours=8))
OUT = "inputs/google_news.json"
CACHE = "inputs/google_resolve.json"
QUERIES = [
    "商圈",
    "老街",
    "商店街",
    "商圈 延期 OR 取消 OR 停辦 OR 改期 OR 順延",
    "商圈 淹水 OR 積水 OR 停電 OR 受損 OR 招牌 OR 停業",
]
WHEN = "2d"
KEEP_DAYS = 3
CACHE_DAYS = 8
MAX_RESOLVE = 120
WORKERS = 3
TIMEOUT = 15
BUDGET_SECONDS = 110
UA = ("Mozilla/5.0 (compatible; district-news-data-fetcher/1.0; "
      "+https://github.com/behaha9/district-news-data)")
# keep a title only when it is about a 商圈 itself, a street/market activity that changed,
# or damage to shops — the same idea as the curation keyword groups
TITLE_KEEP = re.compile(
    r"商圈|老街|商店街"
    r"|(延期|取消|停辦|改期|順延).*(活動|節|祭|市集|嘉年華|踩街|燈會|園遊會)"
    r"|(活動|節|祭|市集|嘉年華|踩街|燈會|園遊會).*(延期|取消|停辦|改期|順延)"
    r"|(淹水|積水|停電|受損|停業|暫停營業|招牌).*(商家|店家|攤商|攤販|市場)"
    r"|(商家|店家|攤商|攤販|市場).*(淹水|積水|停電|受損|停業|暫停營業|招牌)")
TITLE_DROP = re.compile(r"夜市")


def get(url, data=None, headers=None):
    hdrs = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"}
    hdrs.update(headers or {})
    req = Request(url, data=data, headers=hdrs)
    with urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read(2_000_000).decode(resp.headers.get_content_charset() or "utf-8", "replace")


def article_id(link):
    path = urlsplit(link).path
    match = re.search(r"/articles/([^/?#]+)", path)
    return match.group(1) if match else None


def decode_offline(gid):
    """Older Google News ids carry the publisher URL inside the base64 payload."""
    try:
        raw = base64.urlsafe_b64decode(gid + "=" * (-len(gid) % 4))
    except Exception:
        return None
    text = raw.decode("latin-1")
    match = re.search(r"https?://[\x21-\x7e]+", text)
    if not match:
        return None
    url = match.group(0)
    url = re.split(r"[\x00-\x20\x7f-\xff]|\xd2\x01", url)[0]
    if "AU_yqL" in url or len(url) < 12:
        return None
    return url


def decode_online(gid):
    """Newer ids: read the signature from Google's article page, then ask Google's own
    batchexecute endpoint for the original URL (the same call the page makes)."""
    page = get(f"https://news.google.com/rss/articles/{gid}")
    sig = re.search(r'data-n-a-sg="([^"]+)"', page)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sig and ts):
        return None
    inner = json.dumps([
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
         "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        gid, int(ts.group(1)), sig.group(1),
    ], separators=(",", ":"))
    freq = json.dumps([[["Fbv4je", inner, None, "generic"]]], separators=(",", ":"))
    body = urlencode({"f.req": freq}).encode()
    text = get("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body,
               headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    chunk = text.split("\n\n", 1)[1] if "\n\n" in text else text
    try:
        data = json.loads(chunk)
        for row in data:
            if isinstance(row, list) and len(row) > 2 and row[0] == "wrb.fr" and row[2]:
                return json.loads(row[2])[1]
    except Exception:
        pass
    match = re.search(r'\\"(https?://[^"\\]+)\\"', chunk)
    return match.group(1) if match else None


def resolve(link):
    gid = article_id(link)
    if not gid:
        return link, None, "no-id"
    url = decode_offline(gid)
    if url:
        return link, url, "offline"
    try:
        url = decode_online(gid)
    except Exception as exc:
        return link, None, f"error:{getattr(exc, 'code', type(exc).__name__)}"
    if url and url.startswith("http"):
        return link, url, "online"
    return link, None, "unresolved"


def search(query):
    q = f"{query} when:{WHEN}"
    url = "https://news.google.com/rss/search?" + urlencode(
        {"q": q, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}, quote_via=quote)
    root = ET.fromstring(get(url))
    rows = []
    for item in root.iter("item"):
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        site = source_el.get("url", "") if source_el is not None else ""
        title = unescape(item.findtext("title") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(TAIPEI)
        except Exception:
            continue
        rows.append({"title": title, "google_url": (item.findtext("link") or "").strip(),
                     "source": source, "source_site": site,
                     "published_at": when.isoformat(timespec="minutes"), "query": query})
    return rows


def norm_title(title):
    return re.sub(r"[\s\W_]+", "", title)[:40]


def main():
    now = datetime.now(TAIPEI)
    stats = {"queries": 0, "query_errors": [], "raw": 0, "kept": 0}
    found = {}
    for query in QUERIES:
        try:
            rows = search(query)
            stats["queries"] += 1
        except Exception as exc:
            stats["query_errors"].append(f"{query}: {getattr(exc, 'code', type(exc).__name__)}")
            continue
        stats["raw"] += len(rows)
        for row in rows:
            if not TITLE_KEEP.search(row["title"]) or TITLE_DROP.search(row["title"]):
                continue
            key = norm_title(row["title"])
            found.setdefault(key, row)
        time.sleep(1)
    stats["kept"] = len(found)

    try:
        with open(CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    except Exception:
        cache = {}
    oldest = (now - timedelta(days=CACHE_DAYS)).isoformat()
    cache = {k: v for k, v in cache.items() if v.get("at", "") >= oldest}

    todo = []
    for row in found.values():
        hit = cache.get(row["google_url"])
        retry = hit and not hit.get("url") and hit.get("at", "") < (now - timedelta(hours=6)).isoformat()
        if not hit or retry:
            todo.append(row["google_url"])
    todo = todo[:MAX_RESOLVE]
    methods = {}
    pool = ThreadPoolExecutor(WORKERS)
    futures = [pool.submit(resolve, link) for link in todo]
    try:
        for future in as_completed(futures, timeout=BUDGET_SECONDS):
            link, url, how = future.result()
            methods[how.split(":")[0]] = methods.get(how.split(":")[0], 0) + 1
            if how.startswith("error") or how == "unresolved":
                methods.setdefault("examples", [])
                if len(methods["examples"]) < 3:
                    methods["examples"].append(how)
            cache[link] = {"url": url, "how": how, "at": now.isoformat(timespec="minutes")}
    except Exception as exc:
        print("Google 轉址還原時間到，未完成的下次再做：", type(exc).__name__)
    pool.shutdown(wait=False, cancel_futures=True)

    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
    items = []
    for row in found.values():
        if row["published_at"] < cutoff:
            continue
        url = (cache.get(row["google_url"]) or {}).get("url")
        items.append({**row, "url": url or row["google_url"], "resolved": bool(url)})
    items.sort(key=lambda r: r["published_at"], reverse=True)
    stats["resolved"] = sum(1 for i in items if i["resolved"])
    stats["items"] = len(items)
    stats["resolve_this_run"] = methods

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"fetched_at": now.isoformat(timespec="seconds"), "stats": stats, "items": items},
                  fh, ensure_ascii=False, indent=1)
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"Google 新聞：查詢 {stats['queries']}／{len(QUERIES)}，原始 {stats['raw']} 則，"
          f"標題相關 {stats['kept']} 則，保留 {len(items)} 則，已還原原文網址 {stats['resolved']} 則；"
          f"本次還原 {methods}；錯誤 {stats['query_errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
