"""Option scoring. Pure functions over aggregate counts; no per-member reasons exist here."""

NOVELTY_NEW = 1.5


def novelty(category: str, recent_counts: dict[str, int]) -> float:
    """1.5 if the category is absent from the last 10 hangouts, else -0.5 per repeat."""
    n = recent_counts.get(category, 0)
    return NOVELTY_NEW if n == 0 else -0.5 * n


def weather_fit(indoor: bool, rain_likely: bool) -> float:
    if rain_likely:
        return 1.0 if indoor else -2.0
    return 0.0 if indoor else 0.5


def score(avail_frac: float, priority_all_free: bool, novelty_pts: float, weather_pts: float,
          stretch_count: int, worst_travel_min: int) -> float:
    return (3 * avail_frac
            + 2 * (1 if priority_all_free else 0)
            + novelty_pts
            + weather_pts
            - 0.5 * stretch_count
            - max(0, (worst_travel_min - 25) / 10))


def score_candidates(candidates: list[dict], verdicts: dict[str, dict[str, dict]],
                     n_members: int, priority: list[str], recent_counts: dict[str, int],
                     weather_by_window: list[dict]) -> list[dict]:
    """Drop any option with a `no`, score the rest, best first.

    candidates: [{option_id, category, indoor, window, free_members, ...}]
    verdicts:   {member: {option_id: {"fit", "travel_min"}}}
    Returns copies of candidates with aggregate fields: score, all_yes, stretch_count,
    worst_travel_min, avail_count.
    """
    out = []
    for c in candidates:
        fits = [v[c["option_id"]] for v in verdicts.values() if c["option_id"] in v]
        if any(f["fit"] == "no" for f in fits):
            continue
        stretch = sum(1 for f in fits if f["fit"] == "stretch")
        worst = max((int(f["travel_min"]) for f in fits), default=0)
        free = c["free_members"]
        rain = weather_by_window[c["window"]]["rain_likely"]
        s = score(len(free) / max(1, n_members),
                  all(p in free for p in priority),
                  novelty(c["category"], recent_counts),
                  weather_fit(c["indoor"], rain),
                  stretch, worst)
        out.append({**c, "score": round(s, 3), "stretch_count": stretch,
                    "worst_travel_min": worst, "avail_count": len(free),
                    "all_yes": bool(fits) and len(fits) == len(free)
                    and all(f["fit"] == "yes" for f in fits)})
    out.sort(key=lambda x: (-x["score"], x["start"]))
    return out


def top_distinct(scored: list[dict], k: int = 3, window_spread: float = 0.3) -> list[dict]:
    """Top k with distinct categories; a small penalty per reuse of a time window keeps
    near-ties from all landing on the same slot."""
    picked: list[dict] = []
    pool = list(scored)
    while pool and len(picked) < k:
        used_windows = [p["window"] for p in picked]
        best = max(pool, key=lambda c: (c["score"] - window_spread * used_windows.count(c["window"]),
                                        -pool.index(c)))
        picked.append(best)
        pool = [c for c in pool if c["category"] != best["category"]]
    return picked
