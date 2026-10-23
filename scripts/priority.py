"""Hints for spotting 115年重點商圈 names in news titles.

A title matches a district when it contains the full name, an alias, or the
distinctive stem (name without 商圈／形象商圈 and without a leading county/city
part separated by a space), e.g. 「九份老街店家照營業」→ 九份老街商圈.
Two-character stems (天母, 永樂, 中山…) are too common to match on their own,
so they only count as the full name.
"""
import json
import os

_REF = os.path.join(os.path.dirname(__file__), "..", "reference", "priority-districts-115.json")


def _stem(name):
    stem = name.split()[-1]
    for suffix in ("形象商圈", "商圈"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def load():
    with open(_REF, encoding="utf-8") as fh:
        districts = json.load(fh)["districts"]
    patterns = []
    for d in districts:
        keys = {d["name"], *d.get("aliases", [])}
        stem = _stem(d["name"])
        if len(stem) >= 3:
            keys.add(stem)
        patterns.append((d["name"], d["city"], sorted(keys, key=len, reverse=True)))
    return patterns


def hints(title, patterns):
    """Return [(name, city)] of priority districts the title appears to mention."""
    found = []
    for name, city, keys in patterns:
        if any(k in title for k in keys) and "夜市" not in name:
            found.append((name, city))
    return found
