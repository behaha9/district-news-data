#!/usr/bin/env python3
"""Print the next batch of priority districts to search, and advance the rotation.

Usage:
  python3 scripts/next_districts.py            # show next 30 names, grouped 5 per query
  python3 scripts/next_districts.py --advance  # same, and save the new position

Rotation position is stored in data/rotation.json so every district is searched
once per full cycle (289 districts / 30 per morning run ≈ 10 workdays).
"""
import json
import sys

BATCH = 30
PER_QUERY = 5
ROSTER = "reference/priority-districts-115.json"
STATE = "data/rotation.json"


def main(advance):
    # Night markets are out of scope (user rule), so they are never searched.
    roster = [d for d in json.load(open(ROSTER, encoding="utf-8"))["districts"] if "夜市" not in d["name"]]
    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except FileNotFoundError:
        state = {"position": 0, "cycle": 1}
    start = state["position"] % len(roster)
    batch = [roster[(start + i) % len(roster)] for i in range(BATCH)]
    for i in range(0, BATCH, PER_QUERY):
        group = batch[i:i + PER_QUERY]
        print(" OR ".join(f'"{d["name"]}"' for d in group), "｜", "、".join(d["city"] for d in group))
    if advance:
        new_position = start + BATCH
        state["cycle"] += new_position // len(roster)
        state["position"] = new_position % len(roster)
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        print(f"已推進到第 {state['position']} 個（第 {state['cycle']} 輪）")


if __name__ == "__main__":
    main("--advance" in sys.argv)
