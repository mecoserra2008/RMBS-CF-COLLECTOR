"""Business-day calendars and IPD schedules (TARGET2 + optional national holidays)."""
from __future__ import annotations

import datetime as dt
from functools import lru_cache


def easter(y: int) -> dt.date:
    """Anonymous Gregorian algorithm."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = (h + l_ - 7 * m + 114) % 31 + 1
    return dt.date(y, month, day)


@lru_cache(maxsize=None)
def holidays(year: int, cal: str) -> frozenset:
    e = easter(year)
    hs = {dt.date(year, 1, 1), e - dt.timedelta(days=2), e + dt.timedelta(days=1), dt.date(year, 5, 1),
          dt.date(year, 12, 25), dt.date(year, 12, 26)}                       # TARGET2
    if cal in ("PT", "PT+TARGET"):
        hs |= {dt.date(year, 4, 25), dt.date(year, 6, 10), dt.date(year, 8, 15), dt.date(year, 12, 8)}
        if not 2013 <= year <= 2015:                                          # suspended 2013-2015
            hs |= {dt.date(year, 10, 5), dt.date(year, 11, 1), dt.date(year, 12, 1), e + dt.timedelta(days=60)}
    return frozenset(hs)


def is_business_day(d: dt.date, cal: str = "TARGET") -> bool:
    return d.weekday() < 5 and d not in holidays(d.year, cal)


def adjust(d: dt.date, convention: str = "following", cal: str = "TARGET") -> dt.date:
    if convention == "none":
        return d
    x = d
    while not is_business_day(x, cal):
        x += dt.timedelta(days=1)
    if convention == "modified_following" and x.month != d.month:
        x = d
        while not is_business_day(x, cal):
            x -= dt.timedelta(days=1)
    return x


def ipd_schedule(rule: dict, start: dt.date, end: dt.date) -> list[dt.date]:
    """Adjusted IPDs strictly after ``start`` and on/before ``end`` for a rule
    ``{months: [..], day: d, convention: following, calendar: TARGET}``."""
    out = []
    months = sorted(rule["months"])
    y = start.year
    while True:
        for m in months:
            unadj = dt.date(y, m, min(rule["day"], 28 if m == 2 and rule["day"] > 28 else rule["day"]))
            d = adjust(unadj, rule.get("convention", "following"), rule.get("calendar", "TARGET"))
            if d > end:
                return out
            if d > start:
                out.append(d)
        y += 1


def rule_at(rules: list[dict], d: dt.date) -> dict:
    """Pick the IPD rule in force at date d (rules carry optional ``from``)."""
    cur = rules[0]
    for r in rules:
        if "from" in r and dt.date.fromisoformat(str(r["from"])) <= d:
            cur = r
    return cur


def next_ipds(rules: list[dict], start: dt.date, n: int) -> list[dt.date]:
    out: list[dt.date] = []
    cur = start
    while len(out) < n:
        rule = rule_at(rules, cur + dt.timedelta(days=1))
        nxt = ipd_schedule(rule, cur, cur + dt.timedelta(days=400))
        if not nxt:
            raise ValueError("IPD rule produced no dates")
        out.append(nxt[0])
        cur = nxt[0]
    return out


def year_frac(a: dt.date, b: dt.date, basis: str = "ACT/360") -> float:
    days = (b - a).days
    return days / (365.0 if basis.upper().endswith("365") else 360.0)
