"""Planning pipeline pieces: window parsing, candidate windows, grounding, option building."""

import re
from datetime import datetime, timedelta

from group_agent.scoring import novelty, weather_fit
from shared.config import iso, parse_iso

SLOT_HOURS = 2.5
MAX_OPTIONS = 12
COST_BANDS = {"free": 0.0, "$": 15.0, "$$": 35.0, "$$$": 70.0}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ------------------------------------------------------------------ windows

def _at(day: datetime, hh: int, mm: int = 0) -> datetime:
    return day.replace(hour=hh, minute=mm, second=0, microsecond=0)


def _day_window(day: datetime) -> tuple[datetime, datetime]:
    if day.weekday() >= 5:
        return _at(day, 10), _at(day, 23, 30)
    return _at(day, 17), _at(day, 23, 30)


def _clip_now(start: datetime, end: datetime, now: datetime) -> tuple[datetime, datetime] | None:
    floor = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    start = max(start, floor)
    return (start, end) if end - start >= timedelta(hours=SLOT_HOURS) else None


def parse_window_rules(text: str, now: datetime) -> tuple[datetime, datetime] | None:
    """tonight / tomorrow / weekday name / this weekend. None if no rule applies."""
    low = text.lower()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "tonight" in low:
        return _clip_now(_at(today, 17), _at(today, 23, 30), now)
    if "tomorrow" in low:
        d = today + timedelta(days=1)
        return _at(d, 17), _at(d, 23, 30)
    if re.search(r"\b(this|the) weekend\b|\bweekend\b", low):
        nxt = "next weekend" in low
        fri = today + timedelta(days=(4 - today.weekday()) % 7)
        if today.weekday() >= 5:            # already Sat/Sun: this weekend started
            fri = today - timedelta(days=today.weekday() - 4)
        if nxt:
            fri += timedelta(days=7)
        win = _clip_now(_at(fri, 17), _at(fri + timedelta(days=2), 22), now)
        if win is None:                      # weekend is over
            fri += timedelta(days=7)
            win = _at(fri, 17), _at(fri + timedelta(days=2), 22)
        return win
    for i, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b|\b{name[:3]}\b", low):
            d = today + timedelta(days=(i - today.weekday()) % 7)
            win = _clip_now(*_day_window(d), now)
            if win is None:
                win = _day_window(d + timedelta(days=7))
            return win
    return None


def default_window(now: datetime) -> tuple[datetime, datetime]:
    return parse_window_rules("this weekend", now)


def candidate_windows(start: datetime, end: datetime, free_slots: dict[str, list[list[str]]],
                      priority: list[str], k: int = 3) -> list[dict]:
    """2.5 h slots starting on the hour; top k non-overlapping by members free, then earliest.

    free_slots: {member: [[start, end], ...]} for members who replied; others are unknown.
    Priority members must be free; if no slot satisfies that, relax.
    """
    parsed = {m: [(parse_iso(a), parse_iso(b)) for a, b in slots] for m, slots in free_slots.items()}
    dur = timedelta(hours=SLOT_HOURS)
    t = start.replace(minute=0, second=0, microsecond=0)
    if t < start:
        t += timedelta(hours=1)
    slots = []
    while t + dur <= end:
        free = sorted(m for m, ss in parsed.items() if any(a <= t and t + dur <= b for a, b in ss))
        slots.append({"start": t, "end": t + dur, "free": free})
        t += timedelta(hours=1)
    if priority:
        strict = [s for s in slots if all(p in s["free"] for p in priority)]
        if strict:
            slots = strict
    slots.sort(key=lambda s: (-len(s["free"]), s["start"]))
    picked: list[dict] = []
    for s in slots:
        if any(s["start"] < p["end"] and p["start"] < s["end"] for p in picked):
            continue
        picked.append(s)
        if len(picked) == k:
            break
    picked.sort(key=lambda s: s["start"])
    return [{"start": iso(s["start"]), "end": iso(s["end"]), "free_members": s["free"]}
            for s in picked]


# ------------------------------------------------------------------ grounding

def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", s.lower()) if len(t) > 2}


async def ground_ideas(ideas: list[dict], windows: list[dict], events: list[dict],
                       places, home_lat: float, home_lng: float) -> list[dict]:
    """Resolve ideas to real events/places. Ideas that don't resolve are dropped.

    Returns grounded candidates: idea fields + place, lat, lng, cost, fixed_start (events).
    """
    grounded = []
    used_events = set()
    for idea in ideas:
        if idea["source"] == "event":
            q = _tokens(idea["search_query"] + " " + idea["title"])
            for ev in events:
                if ev["name"] in used_events:
                    continue
                if ev["category"] != idea["category"] and not (q & _tokens(ev["name"])):
                    continue
                w = _window_containing(ev["start"], windows)
                if w is None:
                    continue
                used_events.add(ev["name"])
                cost = ev["price_min"] if ev["price_min"] is not None else COST_BANDS[idea["cost_band"]]
                grounded.append({**idea, "title": ev["name"], "category": ev["category"],
                                 "place": ev["place"], "lat": ev["lat"], "lng": ev["lng"],
                                 "cost": cost, "cost_band": _band(cost),
                                 "fixed_start": iso(ev["start"]), "window": w})
                break
            else:
                idea = {**idea, "source": "place"}   # no event in range: try a venue instead
        if idea["source"] == "place":
            found = await places.search(idea["search_query"], idea["category"], home_lat, home_lng)
            if not found:
                continue
            p = found[0]
            band = p.get("price_band") or idea["cost_band"]
            grounded.append({**idea, "place": p["name"], "lat": p["lat"], "lng": p["lng"],
                             "indoor": bool(p.get("indoor", idea["indoor"])),
                             "alcohol_centric": bool(idea["alcohol_centric"] or p.get("alcohol_centric")),
                             "cost": COST_BANDS.get(band, 35.0), "cost_band": band,
                             "fixed_start": None, "window": None})
    return grounded


def _window_containing(when: datetime, windows: list[dict]) -> int | None:
    for i, w in enumerate(windows):
        if parse_iso(w["start"]) <= when <= parse_iso(w["end"]) - timedelta(minutes=30):
            return i
    return None


def _band(cost: float) -> str:
    if cost <= 0:
        return "free"
    if cost <= 20:
        return "$"
    if cost <= 50:
        return "$$"
    return "$$$"


def build_candidates(grounded: list[dict], windows: list[dict], weather: list[dict],
                     recent_counts: dict[str, int], limit: int = MAX_OPTIONS) -> list[dict]:
    """Window x grounded idea, pre-ranked by novelty + weather, capped at `limit`.

    Each candidate carries the public OptionSpec fields plus display fields; it
    never carries anything from a member.
    """
    combos = []
    for gi, g in enumerate(grounded):
        idxs = [g["window"]] if g["window"] is not None else range(len(windows))
        for wi in idxs:
            w = windows[wi]
            start = parse_iso(g["fixed_start"]) if g["fixed_start"] else parse_iso(w["start"])
            end = start + timedelta(hours=min(g["duration_h"], SLOT_HOURS))
            pre = novelty(g["category"], recent_counts) + weather_fit(g["indoor"], weather[wi]["rain_likely"])
            combos.append((pre, wi, g, start, end, gi))
    combos.sort(key=lambda c: (-c[0], c[3]))
    # pass 1: every grounded idea once, in its best window, spreading across windows
    per_window = {i: 0 for i in range(len(windows))}
    ordered, seen = [], set()
    for c in combos:
        if c[5] in seen:
            continue
        mine = [x for x in combos if x[5] == c[5]]
        best = min(mine, key=lambda x: (per_window[x[1]], -x[0], x[3]))
        ordered.append(best)
        seen.add(c[5])
        per_window[best[1]] += 1
    # pass 2: fill with the remaining best combos
    for c in combos:
        if len(ordered) >= limit:
            break
        if c not in ordered:
            ordered.append(c)
    ordered = ordered[:limit]
    out = []
    for n, (_, wi, g, start, end, _gi) in enumerate(ordered, 1):
        out.append({
            "option_id": f"opt_{n}",
            "title": g["title"], "category": g["category"], "place": g["place"],
            "start": iso(start), "end": iso(end),
            "cost_per_person": float(g["cost"]), "cost_band": g["cost_band"],
            "alcohol_centric": bool(g["alcohol_centric"]),
            "late_night": end.hour >= 23 or end.date() > start.date(),
            "lat": float(g["lat"]), "lng": float(g["lng"]), "indoor": bool(g["indoor"]),
            "source": g["source"], "window": wi,
            "free_members": windows[wi]["free_members"],
        })
    return out


OPTION_SPEC_FIELDS = ("option_id", "start", "end", "cost_per_person", "alcohol_centric",
                      "late_night", "lat", "lng", "indoor")


def option_spec_dict(c: dict) -> dict:
    return {k: c[k] for k in OPTION_SPEC_FIELDS}


# ------------------------------------------------------------------ display

def fmt_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p").lower()


def fmt_when(start: str) -> str:
    s = parse_iso(start)
    return f"{s.strftime('%a')} {fmt_time(s)}"
