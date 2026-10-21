#!/usr/bin/env python3
"""Merge a batch of newly curated items into data/latest.json.

Usage: python3 scripts/merge.py new_items.json

new_items.json is a JSON array of items in the schema_version 1 item format.
Rules:
- An incoming item whose id already exists, or that shares any source URL with an
  existing item, is merged into that item (sources de-duplicated by URL, max 10,
  newest first; alert upgraded if the incoming item has one). Title and summary of
  the existing item are kept.
- Otherwise the item is appended.
- Items published more than RETENTION_DAYS ago are dropped from latest.json.
- generated_at is set to the current Asia/Taipei time.
- The items added or updated in this run are also written to
  data/archive/YYYY-MM-DD.json (merged with any earlier runs that day).
Prints a one-line summary: added / merged / dropped / total.
"""
import json
import sys
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))
RETENTION_DAYS = 14
LATEST = "data/latest.json"


def ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def merge_sources(existing, incoming):
    seen = {s["url"]: s for s in existing}
    for source in incoming:
        seen.setdefault(source["url"], source)
    ordered = sorted(seen.values(), key=lambda s: ts(s["published_at"]), reverse=True)
    return ordered[:10]


def merge_into(base, item):
    base["sources"] = merge_sources(base["sources"], item["sources"])
    if item.get("alert") and not base.get("alert"):
        base["alert"] = item["alert"]
    base["cities"] = sorted(set(base.get("cities", [])) | set(item.get("cities", [])))
    names = {d["name"]: d for d in base.get("districts", [])}
    for district in item.get("districts", []):
        names.setdefault(district["name"], district)
    base["districts"] = list(names.values())
    if ts(item["published_at"]) > ts(base["published_at"]):
        base["published_at"] = item["published_at"]


def main(new_path):
    with open(LATEST, encoding="utf-8") as fh:
        data = json.load(fh)
    with open(new_path, encoding="utf-8") as fh:
        incoming = json.load(fh)

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

    now = datetime.now(TAIPEI)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    kept = [i for i in items if ts(i["published_at"]) >= cutoff]
    dropped = len(items) - len(kept)
    kept.sort(key=lambda i: ts(i["published_at"]), reverse=True)
    data["items"] = kept
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
    })
    with open(archive_path, "w", encoding="utf-8") as fh:
        json.dump(archive, fh, ensure_ascii=False, indent=1)

    print(f"新增 {added}／合併 {merged}／過期移除 {dropped}／目前共 {len(kept)} 則")


if __name__ == "__main__":
    main(sys.argv[1])
