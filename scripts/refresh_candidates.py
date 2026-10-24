#!/usr/bin/env python3
"""Make sure inputs/candidates.json is fresh before curating.

Usage: python3 scripts/refresh_candidates.py

GitHub's hourly schedule is unreliable (runs get delayed or dropped), so when the
list is older than FRESH_HOURS this script asks GitHub Actions for a new fetch by
pushing requests/fetch.txt (a push to that file triggers the fetch workflow
within seconds), then waits up to WAIT_SECONDS for the new list to land.

Exit code 0 = list is fresh (or was refreshed); 1 = still stale, continue with the
old list and web search as CURATION.md says.
"""
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone

TAIPEI = timezone(timedelta(hours=8))
FRESH_HOURS = 2
WAIT_SECONDS = 150
POLL_SECONDS = 10


def fetched_at():
    try:
        with open("inputs/status.json", encoding="utf-8") as fh:
            return datetime.fromisoformat(json.load(fh)["fetched_at"])
    except Exception:
        return None


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def main():
    # 排程環境預先 clone 的倉庫可能停在 detached HEAD，先切回 main
    if git("symbolic-ref", "-q", "HEAD").returncode != 0:
        git("fetch", "-q", "origin", "main")
        git("checkout", "-q", "-B", "main", "origin/main")
        print("倉庫原為 detached HEAD，已切回 main。")
    git("pull", "-q", "--rebase", "origin", "main")
    before = fetched_at()
    now = datetime.now(TAIPEI)
    if before and now - before < timedelta(hours=FRESH_HOURS):
        print(f"候選清單是新的（{before:%m/%d %H:%M} 抓取），不用重抓。")
        return 0

    print(f"候選清單過舊（{before:%m/%d %H:%M} 抓取），請 GitHub Actions 重抓…" if before
          else "找不到候選清單，請 GitHub Actions 重抓…")
    with open("requests/fetch.txt", "w", encoding="utf-8") as fh:
        fh.write(f"{now.isoformat(timespec='seconds')}\n")
    git("add", "requests/fetch.txt")
    git("commit", "-q", "-m", f"request: 重抓候選清單 {now:%Y-%m-%d %H:%M}")
    for _ in range(3):
        if git("push", "-q", "origin", "HEAD:main").returncode == 0:
            break
        git("pull", "-q", "--rebase", "origin", "main")
    else:
        print("推送重抓請求失敗，沿用舊清單。")
        return 1

    waited = 0
    while waited < WAIT_SECONDS:
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        git("pull", "-q", "--rebase", "origin", "main")
        after = fetched_at()
        if after and (before is None or after > before):
            print(f"已取得新清單（{after:%m/%d %H:%M} 抓取，等了 {waited} 秒）。")
            return 0
    print(f"等了 {WAIT_SECONDS} 秒仍未取得新清單，沿用舊清單。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
