"""Private profile handling and verdict rules. Nothing here leaves the Member agent
except the (fit, travel_min) pair."""

import logging
from datetime import datetime, timedelta

from shared.config import parse_iso
from shared.geo import travel_minutes

log = logging.getLogger("lore.member.profile")

UNKNOWN_HOME_TRAVEL_MIN = 20   # assumed when the owner hasn't set a home area

EMPTY_PROFILE = {"budget_cap_pp": None, "no_alcohol": False, "latest_end": None,
                 "max_travel_min": None, "other": ""}


def merge_profile(profile: dict, parsed: dict, replace_other: bool = False) -> dict:
    """Apply only the keys the owner mentioned. None removes a limit."""
    out = {**EMPTY_PROFILE, **(profile or {})}
    for key in ("budget_cap_pp", "no_alcohol", "latest_end", "max_travel_min"):
        if key in parsed:
            out[key] = parsed[key] if parsed[key] is not None else EMPTY_PROFILE[key]
    if parsed.get("other"):
        out["other"] = parsed["other"] if replace_other or not out["other"] else \
            f"{out['other']} {parsed['other']}"
    return out


def _fmt_hhmm(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    suffix = "am" if h < 12 else "pm"
    return f"{(h % 12) or 12}:{m:02d} {suffix}"


def readback(profile: dict, home: dict | None = None, has_calendar: bool = False) -> str:
    p = {**EMPTY_PROFILE, **(profile or {})}
    bits = []
    if p["budget_cap_pp"] is not None:
        bits.append(f"budget up to ${p['budget_cap_pp']:g}/person")
    if p["no_alcohol"]:
        bits.append("no alcohol-centric plans")
    if p["latest_end"]:
        bits.append(f"home by {_fmt_hhmm(p['latest_end'])}")
    if p["max_travel_min"] is not None:
        bits.append(f"travel up to {p['max_travel_min']} min")
    if p["other"]:
        bits.append(f"notes: {p['other']}")
    if home:
        bits.append(f"home area: {home['label']}")
    bits.append("calendar: connected (busy times only)" if has_calendar else "calendar: not connected")
    return "Here's what I'll keep private: " + " · ".join(bits) + ". Say 'fix: …' to change it."


def latest_end_dt(option_start: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    dt = option_start.replace(hour=h, minute=m, second=0, microsecond=0)
    if h < 5:  # "home by 1" means 1 am the next morning
        dt += timedelta(days=1)
    return dt


def verdict(option, profile: dict, home: dict | None,
            busy: list[tuple[datetime, datetime]]) -> tuple[str, int, str]:
    """(fit, travel_min, reason). The reason is for the owner's own DEBUG log only."""
    p = {**EMPTY_PROFILE, **(profile or {})}
    start, end = parse_iso(option.start), parse_iso(option.end)
    if home:
        travel = travel_minutes(home["lat"], home["lng"], option.lat, option.lng)
    else:
        travel = UNKNOWN_HOME_TRAVEL_MIN
    cap, max_travel = p["budget_cap_pp"], p["max_travel_min"]

    if p["no_alcohol"] and option.alcohol_centric:
        return "no", travel, "alcohol-centric"
    if any(b_start < end and start < b_end for b_start, b_end in busy):
        return "no", travel, "overlaps a busy block"
    if p["latest_end"] and end + timedelta(minutes=travel) > latest_end_dt(start, p["latest_end"]):
        return "no", travel, "would get home after latest_end"
    if cap is not None and option.cost_per_person > cap * 1.2:
        return "no", travel, "cost > 1.2x budget"
    if max_travel is not None and travel > max_travel * 1.5:
        return "no", travel, "travel > 1.5x max"
    if (cap is not None and option.cost_per_person > cap) or \
            (max_travel is not None and travel > max_travel):
        return "stretch", travel, "slightly over budget or travel"
    return "yes", travel, "fits"
