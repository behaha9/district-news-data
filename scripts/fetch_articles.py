#!/usr/bin/env python3
"""Prefetch article metadata for candidate news (runs in GitHub Actions).

Usage: python3 scripts/fetch_articles.py

For candidates whose titles match the curation keyword groups, open the original
article once and keep only what the curator needs to judge it:
- the publisher's own published time (more reliable than Bing's),
- the site name and the publisher's short description (og:description),
- which counties/cities and which 115年重點商圈 the article body mentions, as counts.
Article text itself is NOT stored (the repository is public); only derived counts.

Writes inputs/articles.json: {"fetched_at": ..., "articles": {url: {...}}}.
Entries are cached for 3 days so each article is fetched once.
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(__file__))
import priority  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))
OUT = "inputs/articles.json"
KEEP_DAYS = 3
MAX_NEW = 80
WORKERS = 8
BUDGET_SECONDS = 90
TIMEOUT = 12
MAX_BYTES = 1_500_000
UA = ("Mozilla/5.0 (compatible; district-news-data-fetcher/1.0; "
      "+https://github.com/behaha9/district-news-data)")
SELECT = re.compile(r"商圈|老街|商店街|延期|取消|停辦|改期|順延|暫停|淹水|積水|停電|受損|停業|招牌|倒塌|掉落")
TIME_KEYS = ("article:published_time", "og:article:published_time", "pubdate", "publishdate",
             "parsely-pub-date", "date", "dc.date", "article:published", "publish-date")
CITY_NAMES = ["台北", "新北", "桃園", "台中", "台南", "高雄", "基隆", "苗栗", "彰化", "南投", "雲林",
              "屏東", "宜蘭", "花蓮", "台東", "澎湖", "金門", "連江", "馬祖"]
CITY_FULL = {"台北": "台北市", "新北": "新北市", "桃園": "桃園市", "台中": "台中市", "台南": "台南市",
             "高雄": "高雄市", "基隆": "基隆市", "苗栗": "苗栗縣", "彰化": "彰化縣", "南投": "南投縣",
             "雲林": "雲林縣", "屏東": "屏東縣", "宜蘭": "宜蘭縣", "花蓮": "花蓮縣", "台東": "台東縣",
             "澎湖": "澎湖縣", "金門": "金門縣", "連江": "連江縣", "馬祖": "連江縣"}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.ld, self.times, self.paras, self.title = {}, [], [], [], ""
        self._skip = 0
        self._in_ld = False
        self._in_p = 0
        self._in_title = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"]
        elif tag == "script":
            self._skip += 1
            self._in_ld = "ld+json" in a.get("type", "")
            self._buf = []
        elif tag in ("style", "noscript"):
            self._skip += 1
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        elif tag in ("p", "h1", "h2"):
            self._in_p += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "script":
            if self._in_ld:
                self.ld.append("".join(self._buf))
            self._in_ld = False
            self._skip = max(0, self._skip - 1)
        elif tag in ("style", "noscript"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("p", "h1", "h2"):
            self._in_p = max(0, self._in_p - 1)
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)
        elif self._skip:
            return
        elif self._in_title:
            self.title += data
        elif self._in_p and data.strip():
            self.paras.append(data.strip())


def to_taipei(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{10,13}", value):
            ts = int(value) / (1000 if len(value) == 13 else 1)
            return datetime.fromtimestamp(ts, TAIPEI).isoformat(timespec="minutes")
        value = value.replace("/", "-").replace(" ", "T", 1).replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TAIPEI)
        if dt.year < 2020:
            return None
        return dt.astimezone(TAIPEI).isoformat(timespec="minutes")
    except Exception:
        return None


def ld_values(blob, key):
    found = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == key:
                    found.append(v)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    try:
        walk(json.loads(blob))
    except Exception:
        pass
    return found


def count_cities(text):
    text = text.replace("臺", "台")
    counts = {}
    for name in CITY_NAMES:
        n = text.count(name)
        if n:
            full = CITY_FULL[name]
            counts[full] = counts.get(full, 0) + n
    for base in ("新竹", "嘉義"):
        n_city = text.count(base + "市") + text.count(base[-1] + "市")
        n_county = text.count(base + "縣") + text.count(base[-1] + "縣")
        n_bare = text.count(base) - text.count(base + "市") - text.count(base + "縣")
        if n_city or n_county or n_bare:
            key = base + ("市" if n_city > n_county else "縣" if n_county > n_city else "")
            counts[key] = counts.get(key, 0) + n_city + n_county + max(0, n_bare)
    return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:5])


def fetch(url, patterns):
    entry = {"checked_at": datetime.now(TAIPEI).isoformat(timespec="minutes")}
    try:
        req = Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"})
        with urlopen(req, timeout=TIMEOUT) as resp:
            entry["status"] = resp.status
            raw = resp.read(MAX_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
        html = raw.decode(charset, errors="replace")
    except Exception as exc:
        code = getattr(exc, "code", None)
        entry["status"] = code or "error"
        entry["error"] = str(exc)[:120]
        return url, entry
    page = Page()
    try:
        page.feed(html)
    except Exception:
        pass
    meta = page.meta
    published = None
    for blob in page.ld:
        for value in ld_values(blob, "datePublished"):
            published = published or to_taipei(value if isinstance(value, str) else "")
    for key in TIME_KEYS:
        published = published or to_taipei(meta.get(key))
    for value in page.times:
        published = published or to_taipei(value)
    desc = (meta.get("og:description") or meta.get("description") or "").strip()
    site = (meta.get("og:site_name") or "").strip()
    if not site:
        for blob in page.ld:
            for pub in ld_values(blob, "publisher"):
                if isinstance(pub, dict) and isinstance(pub.get("name"), str):
                    site = site or pub["name"]
    body = " ".join(page.paras)
    if len(body) < 200:
        body = body + " " + desc
    body = body[:4000]
    text = " ".join([meta.get("og:title", ""), desc, body])
    entry.update({
        "published_at": published,
        "site_name": site[:40] or None,
        "description": desc[:160] or None,
        "cities": count_cities(text),
        "districts": [f"{n}（{c}）" for n, c in priority.hints(text, patterns)][:6],
        "body_chars": len(body),
    })
    return url, entry


def main():
    now = datetime.now(TAIPEI)
    try:
        with open("inputs/candidates.json", encoding="utf-8") as fh:
            items = json.load(fh)["items"]
    except Exception as exc:
        print("沒有候選清單，略過：", exc)
        return 0
    try:
        with open(OUT, encoding="utf-8") as fh:
            cache = json.load(fh).get("articles", {})
    except Exception:
        cache = {}
    cutoff = now - timedelta(days=KEEP_DAYS)
    cache = {u: e for u, e in cache.items()
             if datetime.fromisoformat(e.get("checked_at", "2000-01-01T00:00+08:00")) >= cutoff}

    wanted = []
    for item in items:
        url = item.get("url", "")
        if not url.startswith("https://") or url in wanted or not SELECT.search(item.get("title", "")):
            continue
        old = cache.get(url)
        retry = old and old.get("status") != 200 and \
            datetime.fromisoformat(old["checked_at"]) < now - timedelta(hours=6)
        if not old or retry:
            wanted.append(url)
    wanted = wanted[:MAX_NEW]
    patterns = priority.load()
    done = 0
    pool = ThreadPoolExecutor(WORKERS)
    futures = [pool.submit(fetch, url, patterns) for url in wanted]
    try:
        for future in as_completed(futures, timeout=BUDGET_SECONDS):
            url, entry = future.result()
            cache[url] = entry
            done += 1
    except Exception as exc:  # budget exhausted: keep what we have
        print("原文預抓時間到，未完成的下次再抓：", type(exc).__name__)
    pool.shutdown(wait=False, cancel_futures=True)
    ok = sum(1 for u in wanted if cache.get(u, {}).get("status") == 200)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"fetched_at": now.isoformat(timespec="seconds"), "articles": cache},
                  fh, ensure_ascii=False, indent=1)
    print(f"原文預抓：新抓 {done}／{len(wanted)}，成功 {ok}；快取共 {len(cache)} 篇")
    return 0


if __name__ == "__main__":
    sys.exit(main())
