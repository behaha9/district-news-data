#!/usr/bin/env python3
"""Merge a batch of newly curated items into data/latest.json.

Usage: python3 scripts/merge.py new_items.json

new_items.json is a JSON array of items in the schema_version 1 item format.
Rules:
- An incoming item whose id already exists, or that shares any source URL with an
  existing item, is merged into that item (sources de-duplicated by URL, max 10,
  newest first; alert upgraded if the incoming item has one). Title and summary of
  the existing item are kept.
- Every item is tidied on every run: outlet names normalized (自由時報 variants,
  "X on MSN", known domains), the same article under another host/section dropped,
  sources sorted newest first, and published_at set to the newest source's time
  (the website shows sources[0] as 主要報導 next to published_at).
- Otherwise the item is appended.
- Items published more than RETENTION_DAYS ago are dropped from latest.json.
- generated_at is set to the current Asia/Taipei time.
- The items added or updated in this run are also written to
  data/archive/YYYY-MM-DD.json (merged with any earlier runs that day).
Prints a one-line summary: added / merged / dropped / total.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))
RETENTION_DAYS = 14
LATEST = "data/latest.json"


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


OUTLET_NAMES = {"mknews": "天天上新聞", "truemii.chinatimes": "中時新聞網"}


def outlet_name(name):
    """Normalize outlet names so one outlet is counted once on the website."""
    name = name.strip()
    if name.endswith(" on MSN"):
        name = name[: -len(" on MSN")].strip()
    if name.startswith("自由時報"):
        return "自由時報"
    return OUTLET_NAMES.get(name, name)


def url_key(url):
    """Same article under different hosts/sections counts once (www., LTN sections)."""
    m = re.match(r"https://news\.ltn\.com\.tw/news/[^/]+/breakingnews/(\d+)", url)
    if m:
        return "ltn:" + m.group(1)
    return re.sub(r"^https://www\.", "https://", url.split("#")[0].rstrip("/"))


def merge_sources(existing, incoming):
    seen = {}
    for source in list(existing) + list(incoming):
        source["outlet"] = outlet_name(source["outlet"])
        seen.setdefault(url_key(source["url"]), source)
    ordered = sorted(seen.values(), key=lambda s: ts(s["published_at"]), reverse=True)
    return ordered[:10]


def tidy(item):
    """sources newest first; the card shows sources[0] (主要報導) with published_at,
    so published_at is always the newest source's time."""
    item["sources"] = merge_sources(item["sources"], [])
    item["published_at"] = item["sources"][0]["published_at"]


def merge_into(base, item):
    base["sources"] = merge_sources(base["sources"], item["sources"])
    if item.get("alert") and not base.get("alert"):
        base["alert"] = item["alert"]
    if item.get("admin_note") and not base.get("admin_note"):
        base["admin_note"] = item["admin_note"]
    base["cities"] = sorted(set(base.get("cities", [])) | set(item.get("cities", [])))
    names = {d["name"]: d for d in base.get("districts", [])}
    for district in item.get("districts", []):
        names.setdefault(district["name"], district)
    base["districts"] = list(names.values())


def main(new_path):
    with open(LATEST, encoding="utf-8") as fh:
        data = json.load(fh)
    with open(new_path, encoding="utf-8") as fh:
        incoming = json.load(fh)
    # new_items.json may be a plain list of news items, or
    # {"items": [...], "discussions": [...]} when PTT posts were curated too.
    new_discussions = []
    if isinstance(incoming, dict):
        new_discussions = incoming.get("discussions", [])
        incoming = incoming.get("items", [])

    posts = {p["url"]: p for p in data.get("discussions", [])}
    posts_added = posts_updated = 0
    for post in new_discussions:
        if post["url"] in posts:
            existing = posts[post["url"]]
            existing["push"] = post["push"]  # keep the latest heat
            posts_updated += 1
        else:
            posts[post["url"]] = post
            posts_added += 1

    items = data["items"]
    by_id = {item["id"]: item for item in items}
    by_url = {s["url"]: item for item in items for s in item["sources"]}
    added = merged = 0
    touched = []
    for item in incoming:
        target = by_id.get(item["id"]) or next(
            (by_url[s["url"]] for s in item["sources"] if s["url"] in by_url), None
        )
        if target:
            merge_into(target, item)
            merged += 1
            touched.append(target)
        else:
            items.append(item)
            added += 1
            touched.append(item)
        for owner in (target or item,):
            by_id[owner["id"]] = owner
            for s in owner["sources"]:
                by_url[s["url"]] = owner

    for item in items:
        tidy(item)

    now = datetime.now(TAIPEI)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    kept = [i for i in items if ts(i["published_at"]) >= cutoff]
    dropped = len(items) - len(kept)
    kept.sort(key=lambda i: ts(i["published_at"]), reverse=True)
    data["items"] = kept
    kept_posts = [p for p in posts.values() if ts(p["published_at"]) >= cutoff]
    kept_posts.sort(key=lambda p: (p["push"], ts(p["published_at"])), reverse=True)
    data["discussions"] = kept_posts
    data["generated_at"] = now.replace(microsecond=0).isoformat()
    with open(LATEST, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)

    archive_path = f"data/archive/{now:%Y-%m-%d}.json"
    try:
        with open(archive_path, encoding="utf-8") as fh:
            archive = json.load(fh)
    except FileNotFoundError:
        archive = {"date": f"{now:%Y-%m-%d}", "runs": []}
    archive["runs"].append({
        "at": data["generated_at"],
        "added": added,
        "merged": merged,
        "items": touched,
        "discussions_added": posts_added,
        "discussions_updated": posts_updated,
    })
    with open(archive_path, "w", encoding="utf-8") as fh:
        json.dump(archive, fh, ensure_ascii=False, indent=1)

    print(f"新聞：新增 {added}／合併 {merged}／過期移除 {dropped}／目前共 {len(kept)} 則")
    print(f"PTT：新增 {posts_added}／更新熱度 {posts_updated}／目前共 {len(kept_posts)} 篇")


if __name__ == "__main__":
    main(sys.argv[1])
