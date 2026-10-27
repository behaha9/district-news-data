#!/usr/bin/env python3
"""Prefetch article metadata for candidate news (runs in GitHub Actions).

Usage: python3 scripts/fetch_articles.py

For candidates whose titles match the curation keyword groups, open the original
article once and keep only what the curator needs to judge it:
- the publisher's own published time (more reliable than Bing's),
- the site name and the publisher's short description (og:description),
- which counties/cities and which 115年重點商圈 the article body mentions, as counts,
- the address of the publisher's own share-preview image (og:image), for news thumbnails.
Article text itself is NOT stored (the repository is public); only derived counts.
Images are NOT downloaded or copied; only the publisher's own image address is kept,
so the website can show it as a link preview next to the headline.

Writes inputs/articles.json: {"fetched_at": ..., "articles": {url: {...}}}.
Entries are cached for 3 days so each article is fetched once.
Also writes inputs/thumbs.json: {"updated_at": ..., "images": {article_url: {"src": image_url,
"seen": "YYYY-MM-DD"}}}, kept for 8 days, for the website's news thumbnails.
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(__file__))
import priority  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))
OUT = "inputs/articles.json"
THUMBS = "inputs/thumbs.json"
LATEST = "data/latest.json"
KEEP_DAYS = 3
THUMB_DAYS = 8
OLD_DAYS = 7  # an article dated more than a week before the run is treated as old news
SITE_DEFAULT_REPEAT = 3  # the same image on 3+ articles of one site = the site's default picture
MAX_NEW = 250
WORKERS = 8
BUDGET_SECONDS = 90
TIMEOUT = 12
MAX_BYTES = 1_500_000
UA = ("Mozilla/5.0 (compatible; district-news-data-fetcher/1.0; "
      "+https://github.com/behaha9/district-news-data)")
SELECT = re.compile(r"商圈|老街|商店街|延期|取消|停辦|改期|順延|暫停|淹水|積水|停電|受損|停業|招牌|倒塌|掉落")
TIME_KEYS = ("article:published_time", "og:article:published_time", "pubdate", "publishdate",
             "parsely-pub-date", "date", "dc.date", "article:published", "publish-date")
IMAGE_KEYS = ("og:image:secure_url", "og:image", "og:image:url", "twitter:image",
              "twitter:image:src", "image", "thumbnailurl")
BAD_IMAGE = re.compile(r"logo|placeholder|no[-_]?image|noimg|default|favicon|blank|spacer|1x1|\.svg(\?|$)"
                       r"|(share|og[-_]?image|fb[-_]?image)\.(jpe?g|png|webp|gif)$", re.I)
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


def clean_image(value, page_url):
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or ""
    if isinstance(value, list):
        value = value[0] if value else ""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.startswith("data:"):
        return None
    value = urljoin(page_url, value)
    if value.startswith("http://"):
        value = "https://" + value[7:]  # pages are https; the website falls back to an icon if this fails
    if not value.startswith("https://") or len(value) > 500 or re.search(r"[\s\x00-\x1f\"'<>]", value):
        return None
    if BAD_IMAGE.search(urlsplit(value).path):
        return None
    return value


def pick_image(page, page_url):
    for key in IMAGE_KEYS:
        found = clean_image(page.meta.get(key), page_url)
        if found:
            return found
    for blob in page.ld:
        for value in ld_values(blob, "image") + ld_values(blob, "thumbnailUrl"):
            found = clean_image(value, page_url)
            if found:
                return found
    return None


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


DATE_IN_PATH = re.compile(r"(?<!\d)(20\d{2})[/_-]?(0[1-9]|1[0-2])[/_-]?(0[1-9]|[12]\d|3[01])(?!\d)")


def date_hint(published, image, url):
    """Best guess of the article's date: the publisher's time, else a date in the image path
    (e.g. /newsimages/2016/09/27/), else a date in the article URL. Catches old articles that
    news search engines re-list with today's date."""
    if published:
        return published[:10], "原文時間"
    for value, label in ((image, "圖片路徑"), (url, "網址")):
        match = DATE_IN_PATH.search(urlsplit(value or "").path)
        if match:
            return "-".join(match.groups()), label
    return None, None


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
        "image": pick_image(page, url),
    })
    entry["date_hint"], entry["date_hint_from"] = date_hint(published, entry["image"], url)
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

    try:
        with open("inputs/district_news.json", encoding="utf-8") as fh:
            district_items = json.load(fh).get("items", [])
    except Exception:
        district_items = []
    try:
        with open(LATEST, encoding="utf-8") as fh:
            curated = [{"url": s["url"], "district": True}
                       for it in json.load(fh).get("items", []) for s in it.get("sources", [])]
    except Exception:
        curated = []
    try:
        with open("inputs/google_news.json", encoding="utf-8") as fh:
            google = [{"url": g["url"], "district": True}
                      for g in json.load(fh).get("items", []) if g.get("resolved")]
    except Exception:
        google = []
    wanted = []
    for item in curated + district_items + google + items:
        url = item.get("url", "")
        relevant = item.get("district") or SELECT.search(item.get("title", ""))
        if not url.startswith("https://") or url in wanted or not relevant:
            continue
        old = cache.get(url)
        retry = old and old.get("status") != 200 and \
            datetime.fromisoformat(old["checked_at"]) < now - timedelta(hours=6)
        if not old or retry:
            wanted.append(url)
    # articles fetched before thumbnails existed: fetch again once to learn their image
    backfill = [u for u, e in cache.items() if e.get("status") == 200 and ("image" not in e or "date_hint" not in e)]
    wanted = (wanted + [u for u in backfill if u not in wanted])[:MAX_NEW]
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
    shown = write_thumbs(cache, now)
    drop_old_google(cache, now)
    print(f"原文預抓：新抓 {done}／{len(wanted)}，成功 {ok}；快取共 {len(cache)} 篇；縮圖 {shown} 張")
    return 0


def drop_old_google(cache, now):
    """Google News sometimes re-lists old articles with a fresh date. Remove items whose own
    article date (publisher time, image path or URL date) is more than OLD_DAYS before the run."""
    path = "inputs/google_news.json"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return
    oldest = (now - timedelta(days=OLD_DAYS)).strftime("%Y-%m-%d")
    # an image path date alone is weaker evidence (an old file photo can be reused), so it only
    # drops an item when it is very old; the curator still sees a warning for anything > 7 days
    oldest_by_image = (now - timedelta(days=180)).strftime("%Y-%m-%d")
    keep, dropped = [], []
    for item in data.get("items", []):
        art = cache.get(item.get("url")) or {}
        hint = art.get("date_hint")
        limit = oldest_by_image if art.get("date_hint_from") == "圖片路徑" else oldest
        if hint and hint < limit:
            dropped.append(f"{item.get('title', '')[:40]}（原文日期 {hint}）")
        else:
            keep.append(item)
    if dropped:
        data["items"] = keep
        data.setdefault("stats", {})["dropped_old"] = dropped
        data["stats"]["items"] = len(keep)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print("Google 新聞移除舊聞：", "；".join(dropped))


def write_thumbs(cache, now):
    today = now.strftime("%Y-%m-%d")
    try:
        with open(THUMBS, encoding="utf-8") as fh:
            images = json.load(fh).get("images", {})
    except Exception:
        images = {}
    for url, entry in cache.items():
        if entry.get("image") and not BAD_IMAGE.search(urlsplit(entry["image"]).path):
            seen = images.get(url, {}).get("seen") or today
            images[url] = {"src": entry["image"], "seen": seen}
        elif "image" in entry:  # checked: no usable image (or only the site's default picture)
            images.pop(url, None)
    oldest = (now - timedelta(days=THUMB_DAYS)).strftime("%Y-%m-%d")
    images = {u: v for u, v in images.items() if v.get("seen", "") >= oldest}
    repeats = {}
    for url, value in images.items():
        key = (urlsplit(url).hostname, value["src"])
        repeats[key] = repeats.get(key, 0) + 1
    images = {u: v for u, v in images.items()
              if repeats[(urlsplit(u).hostname, v["src"])] < SITE_DEFAULT_REPEAT}
    with open(THUMBS, "w", encoding="utf-8") as fh:
        json.dump({"updated_at": now.isoformat(timespec="seconds"), "images": images},
                  fh, ensure_ascii=False, separators=(",", ":"))
    return len(images)


if __name__ == "__main__":
    sys.exit(main())
