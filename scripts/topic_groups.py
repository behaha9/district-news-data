#!/usr/bin/env python3
"""Check data/topic-groups.json (關注議題「同一件事合併」對照表) and list articles not yet grouped.

Usage: python3 scripts/topic_groups.py
Exit code 0 = valid (未分組 may still be > 0; 要處理到 0 再提交), 1 = errors found.

Rules (same as the website):
- Articles are matched by their original URL (inputs/topics.json -> topics[].items[].url).
- Each group: unique id, non-empty urls, representative must be one of its urls.
- A URL may appear in only one place (one group, or exclude).
- Only active topics in reference/topics.json are checked; URLs no longer in inputs are warnings
  (articles older than 60 days drop out of inputs and the site simply ignores them).
"""
import json
import re
import sys

sys.dont_write_bytecode = True

GROUPS = "data/topic-groups.json"
INPUTS = "inputs/topics.json"
CONFIG = "reference/topics.json"
ID_RE = re.compile(r"^\d{8}-[a-z0-9-]+$")
AI_ANSWER_RE = re.compile(r"/news/ai/answer/|/ai/answer/", re.I)
NON_TW_HOSTS = ("crntt.", "chinatimes.hk", "huanqiu.", "xinhuanet.", "people.com.cn", "cctv.")


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def main():
    errors, warnings = [], []
    data = load(GROUPS)
    if data is None:
        print(f"✗ 找不到 {GROUPS}")
        return 1
    if data.get("schema_version") != 1:
        errors.append("schema_version 必須是 1")
    if not isinstance(data.get("updated_at"), str):
        errors.append("缺少 updated_at（ISO 8601，臺北時間）")

    active = {t["id"]: t for t in load(CONFIG, {}).get("topics", []) if t.get("active")}
    inputs = {t["id"]: t for t in load(INPUTS, {}).get("topics", [])}
    topics = data.get("topics", {})
    if not isinstance(topics, dict):
        print("✗ topics 必須是物件（以議題 id 為鍵）")
        return 1

    total_unassigned = 0
    for topic_id in sorted(set(active) | set(topics)):
        entry = topics.get(topic_id, {"groups": [], "exclude": []})
        items = {it["url"]: it for it in inputs.get(topic_id, {}).get("items", []) if it.get("url")}
        name = active.get(topic_id, {}).get("name", topic_id)
        seen, ids = {}, set()

        def claim(url, where):
            if url in seen:
                errors.append(f"[{name}] 網址重複出現在 {seen[url]} 與 {where}：{url}")
            seen[url] = where
            if url not in items:
                warnings.append(f"[{name}] {where} 的網址不在目前 inputs（可能已過期）：{url}")

        for g in entry.get("groups", []):
            gid = g.get("id", "")
            if not ID_RE.match(gid):
                errors.append(f"[{name}] 組別 id 格式應為 YYYYMMDD-英文短代號：{gid!r}")
            if gid in ids:
                errors.append(f"[{name}] 組別 id 重複：{gid}")
            ids.add(gid)
            urls = g.get("urls") or []
            if not urls:
                errors.append(f"[{name}] {gid} 沒有任何網址")
            if g.get("representative") not in urls:
                errors.append(f"[{name}] {gid} 的 representative 必須是 urls 其中之一")
            for url in urls:
                claim(url, f"組 {gid}")
                if AI_ANSWER_RE.search(url):
                    errors.append(f"[{name}] {gid} 含 AI 問答頁（應排除）：{url}")
        for ex in entry.get("exclude", []):
            if not ex.get("reason"):
                errors.append(f"[{name}] 排除項目缺 reason：{ex.get('url')}")
            claim(ex.get("url", ""), "排除")

        unassigned = [it for url, it in items.items() if url not in seen]
        unassigned.sort(key=lambda it: it.get("published_at", ""), reverse=True)
        total_unassigned += len(unassigned)
        groups_n = len(entry.get("groups", []))
        print(f"【{name}】文章 {len(items)} 篇｜{groups_n} 則｜排除 {len(entry.get('exclude', []))}｜未分組 {len(unassigned)}")
        for g in sorted(entry.get("groups", []), key=lambda g: items.get(g.get("representative"), {}).get("published_at", ""), reverse=True):
            rep = items.get(g.get("representative"), {})
            print(f"  · {g.get('id')}｜{len(g.get('urls', []))} 篇｜{rep.get('published_at', '')[:10]}｜{rep.get('title', '（代表原文已不在 inputs）')[:40]}")
        for it in unassigned:
            hint = ""
            if AI_ANSWER_RE.search(it["url"]):
                hint = "  ⚠ 疑似 AI 問答頁 → 排除"
            elif any(h in it["url"] for h in NON_TW_HOSTS):
                hint = "  ⚠ 疑似非臺灣媒體 → 排除"
            print(f"  ＋未分組 {it.get('published_at', '')[:16]}｜{it.get('source', '')}｜{it.get('title', '')[:50]}{hint}")
            print(f"    {it['url']}")

    for w in warnings:
        print("  注意：" + w)
    if errors:
        for e in errors:
            print("✗ " + e)
        return 1
    print(f"✓ 對照表驗證通過；未分組 {total_unassigned} 篇" + ("" if total_unassigned == 0 else "（請依 CURATION.md 四之一處理到 0 再提交）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
