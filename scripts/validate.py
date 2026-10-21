#!/usr/bin/env python3
"""Validate data/latest.json against schema_version 1 (same rules the website applies).

Usage: python3 scripts/validate.py [path]   (default: data/latest.json)
Exit code 0 = valid, 1 = errors found.
"""
import json
import sys
from datetime import datetime

CATEGORIES = {"活動與街區", "開店與消費", "營運與政策", "商圈動向"}
ALERTS = {None, "活動異動", "商圈受災"}
MAX_ITEMS = 500


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    errors = []
    if data.get("schema_version") != 1:
        errors.append("schema_version 必須為 1")
    try:
        parse_time(data["generated_at"])
    except Exception:
        errors.append("generated_at 格式錯誤")
    items = data.get("items", [])
    if len(items) > MAX_ITEMS:
        errors.append(f"items 超過 {MAX_ITEMS} 筆")
    ids = set()
    for item in items:
        iid = item.get("id", "?")
        if iid in ids:
            errors.append(f"{iid}: id 重複")
        ids.add(iid)
        title = item.get("title", "")
        if not title or len(title) > 80:
            errors.append(f"{iid}: title 空白或超過 80 字")
        if "夜市" in title:
            errors.append(f"{iid}: 標題含夜市（依使用者規則不收）")
        if len(item.get("summary", "")) > 120:
            errors.append(f"{iid}: summary 超過 120 字")
        note = item.get("admin_note")
        if note is not None:
            if not isinstance(note, str) or not note.strip() or len(note) > 80:
                errors.append(f"{iid}: admin_note 須為 1–80 字的文字")
            if not any(d.get("priority") for d in item.get("districts", [])):
                errors.append(f"{iid}: admin_note 只用於重點商圈新聞")
        if item.get("category") not in CATEGORIES:
            errors.append(f"{iid}: category 不在允許清單")
        if item.get("alert") not in ALERTS:
            errors.append(f"{iid}: alert 不在允許清單")
        try:
            parse_time(item["published_at"])
        except Exception:
            errors.append(f"{iid}: published_at 格式錯誤")
        if not isinstance(item.get("cities"), list) or not isinstance(item.get("districts"), list):
            errors.append(f"{iid}: cities / districts 必須是陣列")
        sources = item.get("sources", [])
        if not 1 <= len(sources) <= 10:
            errors.append(f"{iid}: sources 須 1–10 筆")
        for source in sources:
            if not str(source.get("url", "")).startswith("https://"):
                errors.append(f"{iid}: 網址不是 https：{source.get('url')}")
            if not source.get("outlet"):
                errors.append(f"{iid}: 缺媒體名稱")
    if errors:
        print("驗證失敗：")
        for err in errors:
            print(" -", err)
        return 1
    print(f"驗證通過：{len(items)} 筆")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data/latest.json"))
