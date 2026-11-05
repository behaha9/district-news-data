#!/usr/bin/env python3
"""首頁審查（CURATION.md 四之二）：確認網站首頁近 7 天每一篇都有 AI 的結論。

Usage:
  python3 scripts/review.py                    # 檢查，列出未審查的首頁文章
  python3 scripts/review.py review_new.json    # 先寫入本輪的審查決定，再檢查

review_new.json（放倉庫根目錄、不要提交），以原文網址為鍵：
  {
    "<網址>": {"decision": "hidden", "reason": "選舉"},
    "<網址>": {"decision": "kept", "item": "20261104-yongle-construction-inconvenience"}
  }
`at`（審查日期）自動填今天。同一網址再寫一次會覆蓋舊決定。

首頁每一篇必須是下列其一（網站 v58b 用同一套規則）：
  1. 已在 data/latest.json 某一則的 sources 裡 → 自動算收錄，不必記；
  2. kept：收進某一則（sources 已滿 10 筆放不下、或網址寫法不同時用）；
  3. hidden：隱藏，reason 只能是 REASONS 其中一個（網站直接顯示）。

每次執行會自動：
  - 刪掉「網址已在 sources 裡」的重複紀錄；
  - 網址只是寫法不同（www、結尾斜線、#、utm 參數、編碼）但已在某一則 sources 的，自動記 kept；
  - 刪掉審查日期超過 14 天的紀錄（與新聞資料保存期一致）。
內容有變才改寫 data/review.json 並更新 updated_at。

結束碼：0＝通過（未審查 0 篇，可提交）
        1＝格式錯誤（不可提交，修正後重跑）
        2＝還有未審查（處理到 0 再提交）
        3＝首頁清單缺漏或超過 6 小時（本輪無法審查；照常提交，回報時註明）
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

sys.dont_write_bytecode = True

TAIPEI = timezone(timedelta(hours=8))
REVIEW = "data/review.json"
HOMEPAGE = "inputs/homepage.json"
LATEST = "data/latest.json"
ARTICLES = "inputs/articles.json"
KEEP_DAYS = 14
STALE_HOURS = 6
REASONS = ["選舉", "治安車禍", "國外", "房地產", "業配遊記", "企業人事", "夜市", "非商圈", "舊聞"]
DECISIONS = {"kept", "hidden"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HINTS = [
    (re.compile(r"夜市"), "夜市"),
    (re.compile(r"參選|候選人|政見|競選|拜票|掃街|車掃|造勢|辯論"), "選舉"),
    (re.compile(r"建案|房價|房市|房仲|不動產|預售|地王|獵地|推案|都更"), "房地產"),
    (re.compile(r"董事長|董座|總經理|執行長|人事異動|(接任|出任)(董|總|執)"), "企業人事"),
    (re.compile(r"車禍|擦撞|酒駕|竊|詐騙|持刀|鬥毆|查獲|警方|失蹤|走失"), "治安車禍"),
    (re.compile(r"日本|韓國|大陸|中國|無錫|上海|北京|香港|澳門|泰國|越南|新加坡|美國|歐洲"), "國外"),
]


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def norm(url):
    """網址寫法不同但同一篇：去 scheme、www.、#、結尾斜線、utm 參數，解碼後比較。"""
    try:
        parts = urlsplit(unquote(url.strip()))
    except ValueError:
        return url
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    path = re.sub(r"/amp(?=/)", "", parts.path, count=1)  # AMP 版與一般版同一篇
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(("utm_", "fbclid"))])
    msn = re.search(r"/(ar|vi)-([A-Za-z0-9]+)$", path.rstrip("/"))
    if host.endswith("msn.com") and msn:  # MSN 同一篇會掛在不同分類路徑
        return f"msn:{msn.group(1)}-{msn.group(2)}"
    m = re.match(r"/news/[^/]+/breakingnews/(\d+)", path)
    if host == "news.ltn.com.tw" and m:
        return "ltn:" + m.group(1)
    return f"{host}{path.rstrip('/')}" + (f"?{query}" if query else "")


def short(text, n):
    text = re.sub(r"\s+", " ", text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def main(new_path=None):
    now = datetime.now(TAIPEI)
    today = now.strftime("%Y-%m-%d")
    errors, notes = [], []

    review = load(REVIEW, None)
    original = json.dumps(review, ensure_ascii=False, sort_keys=True) if review is not None else None
    if review is None:
        review = {"schema_version": 1, "updated_at": "", "reviewed": {}}
        notes.append(f"建立新檔 {REVIEW}")
    if review.get("schema_version") != 1:
        errors.append("schema_version 必須是 1")
    reviewed = review.get("reviewed")
    if not isinstance(reviewed, dict):
        print("✗ reviewed 必須是物件（以原文網址為鍵）")
        return 1

    latest = load(LATEST, {"items": []})
    item_ids = {it["id"] for it in latest.get("items", [])}
    by_source = {}
    by_norm = {}
    for it in latest.get("items", []):
        for s in it.get("sources", []):
            by_source[s["url"]] = it["id"]
            by_norm.setdefault(norm(s["url"]), it["id"])

    # 1. 寫入本輪決定
    if new_path:
        incoming = load(new_path, None)
        if not isinstance(incoming, dict):
            print(f"✗ {new_path} 必須是以網址為鍵的物件")
            return 1
        for url, entry in incoming.items():
            if not isinstance(entry, dict):
                errors.append(f"{new_path}：{url} 的內容必須是物件")
                continue
            entry = {k: v for k, v in entry.items() if k in ("decision", "reason", "item")}
            entry["at"] = today
            reviewed[url] = entry
        notes.append(f"寫入本輪決定 {len(incoming)} 筆")

    # 2. 自動清理
    dup = [u for u in reviewed if u in by_source]
    for u in dup:
        del reviewed[u]
    if dup:
        notes.append(f"網址已在 AI 新聞 sources 裡，移除重複紀錄 {len(dup)} 筆")
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    old = [u for u, e in reviewed.items() if isinstance(e, dict) and str(e.get("at", "9999")) < cutoff]
    for u in old:
        del reviewed[u]
    if old:
        notes.append(f"移除超過 {KEEP_DAYS} 天的紀錄 {len(old)} 筆")

    homepage = load(HOMEPAGE, None)
    stale = None
    if not homepage or not isinstance(homepage.get("items"), list):
        stale = f"找不到 {HOMEPAGE}"
        home_items = []
    else:
        home_items = homepage["items"]
        try:
            age = now - datetime.fromisoformat(homepage["fetched_at"])
            if age > timedelta(hours=STALE_HOURS):
                stale = f"首頁清單是 {age.total_seconds() / 3600:.0f} 小時前抓的（超過 {STALE_HOURS} 小時）"
        except Exception:
            stale = "首頁清單缺 fetched_at"
    on_home = {h["url"] for h in home_items}

    auto = 0
    for h in home_items:
        u = h["url"]
        if u in by_source or u in reviewed:
            continue
        iid = by_norm.get(norm(u))
        if iid:
            reviewed[u] = {"decision": "kept", "item": iid, "at": today}
            auto += 1
    if auto:
        notes.append(f"網址寫法不同但已收錄，自動記 kept {auto} 筆")
    reviewed_norm = {norm(u): e for u, e in reviewed.items() if isinstance(e, dict)}
    copied = 0
    for h in home_items:
        u = h["url"]
        if u in by_source or u in reviewed:
            continue
        e = reviewed_norm.get(norm(u))
        if e and e.get("decision") in DECISIONS:
            reviewed[u] = {k: v for k, v in e.items() if k != "at"} | {"at": today}
            copied += 1
    if copied:
        notes.append(f"網址寫法不同但已審查過，沿用原決定 {copied} 筆")

    # 3. 檢查格式
    for url, e in reviewed.items():
        if not url.startswith("http"):
            errors.append(f"網址格式錯誤：{url}")
        if not isinstance(e, dict):
            errors.append(f"紀錄必須是物件：{url}")
            continue
        d = e.get("decision")
        if d not in DECISIONS:
            errors.append(f"decision 只能是 kept 或 hidden：{url}")
        elif d == "hidden":
            if e.get("reason") not in REASONS:
                errors.append(f"隱藏原因必須是 {'、'.join(REASONS)} 其中之一（目前：{e.get('reason')!r}）：{url}")
            if "item" in e:
                errors.append(f"hidden 不可同時有 item：{url}")
        elif d == "kept":
            if "reason" in e:
                errors.append(f"kept 不可有 reason：{url}")
            if e.get("item") not in item_ids:
                msg = f"kept 指向的 AI 新聞 id 不存在：{e.get('item')!r}｜{url}"
                (errors if url in on_home else notes).append(msg + ("" if url in on_home else "（已不在首頁，不影響網站）"))
        if not DATE_RE.match(str(e.get("at", ""))):
            errors.append(f"at 必須是 YYYY-MM-DD：{url}")

    # 4. 首頁逐篇狀態
    events_covered = {}
    for h in home_items:
        iid = by_source.get(h["url"]) or (reviewed.get(h["url"], {}).get("item"))
        if iid:
            events_covered.setdefault(h.get("event_key"), iid)
    counts = {"sources": 0, "kept": 0, "hidden": 0}
    reasons = {}
    pending = []
    for h in home_items:
        u = h["url"]
        if u in by_source:
            counts["sources"] += 1
        elif u in reviewed and reviewed[u].get("decision") in DECISIONS:
            d = reviewed[u]["decision"]
            counts[d] += 1
            if d == "hidden":
                reasons[reviewed[u].get("reason")] = reasons.get(reviewed[u].get("reason"), 0) + 1
        else:
            pending.append(h)

    # 5. 有變才寫檔
    reviewed = dict(sorted(reviewed.items()))
    review["reviewed"] = reviewed
    if json.dumps(review, ensure_ascii=False, sort_keys=True) != original:
        review["updated_at"] = now.replace(microsecond=0).isoformat()
        if not errors:
            with open(REVIEW, "w", encoding="utf-8") as fh:
                json.dump(review, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
            notes.append(f"已更新 {REVIEW}")
        else:
            notes.append(f"有錯誤，未寫入 {REVIEW}")

    # 6. 報告
    for n in notes:
        print("· " + n)
    if errors:
        for e in errors:
            print("✗ " + e)
        return 1
    if stale:
        print(f"⚠ {stale}：本輪無法審查首頁，照常提交，回報時註明「首頁審查：清單過舊未審」。")
        print(f"  審查紀錄共 {len(reviewed)} 筆，格式正確。")
        return 3

    hid = "、".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
    print(f"首頁 {len(home_items)} 篇（{homepage.get('event_count')} 件）｜在 AI sources {counts['sources']}"
          f"｜kept {counts['kept']}｜隱藏 {counts['hidden']}" + (f"（{hid}）" if hid else "") + f"｜未審查 {len(pending)}")
    articles = load(ARTICLES, {}).get("articles", {})
    for h in sorted(pending, key=lambda x: x.get("published_at", ""), reverse=True):
        print(f"  ＋未審查 {h.get('published_at', '')[:16].replace('T', ' ')}｜{h.get('source', '')}｜{h.get('title', '')}")
        print(f"    {h['url']}")
        tips = []
        same = events_covered.get(h.get("event_key"))
        if same:
            tips.append(f"同事件已收在 {same} → 加進該則 sources，放不下就記 kept")
        text = h.get("title", "") + " " + h.get("summary", "")
        guess = [reason for pattern, reason in HINTS if pattern.search(text)]
        if guess:
            tips.append("可能是：" + "／".join(dict.fromkeys(guess)) + "（仍要自己判斷）")
        art = articles.get(h["url"], {})
        if art.get("date_hint"):
            tips.append(str(art["date_hint"]))
        place = "、".join((h.get("cities") or []) + (h.get("districts") or []))
        if place:
            tips.append("網站判定地點：" + place)
        if tips:
            print("    ↳ " + "；".join(tips))
        summary = h.get("summary") or art.get("description") or ""
        if summary:
            print("    ↳ 摘要：" + short(summary, 90))
    if pending:
        print(f"還有 {len(pending)} 篇未審查：依 CURATION.md 四之二處理到 0 再提交。")
        return 2
    print("✓ 首頁審查完成；未審查 0 篇")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
