"""iCal fetch, recurring expansion and free-slot computation. Busy times only:
titles, descriptions, locations and attendees are stripped at parse time."""

import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import icalendar
import recurring_ical_events

from shared.config import TZ, iso

log = logging.getLogger("lore.member.calendar")

FIXTURES = Path(__file__).parent / "fixtures"
CACHE_SECONDS = 300
SOCIAL_START = (10, 0)
SOCIAL_END = (23, 59)
QUANTUM_MIN = 30          # slot edges snap inward to :00/:30 so exact private times never leave
PRIVATE_KEYS = ("SUMMARY", "DESCRIPTION", "LOCATION", "ATTENDEE", "ORGANIZER", "COMMENT",
                "CATEGORIES", "URL", "CONTACT")


async def fetch_ics(url: str) -> bytes:
    if url.startswith("fixture:"):
        return (FIXTURES / f"{url.split(':', 1)[1]}.ics").read_bytes()
    if url.startswith("file://") or Path(url).exists():
        return Path(url.removeprefix("file://")).read_bytes()
    if url.startswith("webcal://"):
        url = "https://" + url.removeprefix("webcal://")
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


def _to_dt(v) -> datetime:
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=TZ)).astimezone(TZ)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=TZ)
    raise TypeError(f"unsupported date value {v!r}")


def busy_blocks(ics: bytes, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    cal = icalendar.Calendar.from_ical(ics)
    for comp in cal.walk("VEVENT"):
        for key in PRIVATE_KEYS:
            comp.pop(key, None)
    blocks = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
            continue
        s = _to_dt(ev["DTSTART"].dt)
        if ev.get("DTEND") is not None:
            e = _to_dt(ev["DTEND"].dt)
        elif ev.get("DURATION") is not None:
            e = s + ev["DURATION"].dt
        else:
            e = s + (timedelta(days=1) if not isinstance(ev["DTSTART"].dt, datetime) else timedelta(0))
        blocks.append((s, e))
    blocks.sort()
    return blocks


class CalendarReader:
    """Fetch + parse with a 5-minute cache of the raw calendar."""

    def __init__(self):
        self._cache: dict[str, tuple[float, bytes]] = {}

    async def raw(self, url: str) -> bytes:
        hit = self._cache.get(url)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        data = await fetch_ics(url)
        icalendar.Calendar.from_ical(data)  # validate before caching
        self._cache[url] = (time.monotonic(), data)
        return data

    async def busy(self, url: str | None, start: datetime, end: datetime):
        """Busy blocks, or None if there is no readable calendar."""
        if not url:
            return None
        try:
            return busy_blocks(await self.raw(url), start, end)
        except Exception as exc:
            log.warning("calendar unreadable (%s); treating as no calendar", type(exc).__name__)
            return None


def _snap_up(dt: datetime) -> datetime:
    m = (dt.minute + QUANTUM_MIN - 1) // QUANTUM_MIN * QUANTUM_MIN
    return dt.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=m)


def _snap_down(dt: datetime) -> datetime:
    return dt.replace(minute=dt.minute // QUANTUM_MIN * QUANTUM_MIN, second=0, microsecond=0)


def _day_bounds(day: datetime, latest_end: str | None, default_hours: bool) -> tuple[datetime, datetime]:
    if default_hours:  # no calendar: weekday evenings, weekend days
        s = day.replace(hour=17 if day.weekday() < 5 else 10, minute=0)
        e = day.replace(hour=23, minute=0)
    else:
        s = day.replace(hour=SOCIAL_START[0], minute=SOCIAL_START[1])
        e = day.replace(hour=SOCIAL_END[0], minute=SOCIAL_END[1])
    if latest_end:
        h, m = map(int, latest_end.split(":"))
        if h >= 5:  # small-hours curfews are later than social hours anyway
            e = min(e, day.replace(hour=h, minute=m))
    return s, e


def free_slots(busy: list[tuple[datetime, datetime]] | None, window_start: datetime,
               window_end: datetime, latest_end: str | None) -> list[list[str]]:
    """Free [[start, end]] ISO pairs inside the window, social hours and latest_end."""
    default_hours = busy is None
    busy = busy or []
    out = []
    day = window_start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < window_end:
        ds, de = _day_bounds(day, latest_end, default_hours)
        cur, stop = max(ds, window_start), min(de, window_end)
        for bs, be in busy:
            if be <= cur or bs >= stop:
                continue
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
        if cur < stop:
            out.append((cur, stop))
        day += timedelta(days=1)
    slots = []
    for s, e in out:
        s, e = _snap_up(s), _snap_down(e)
        if e - s >= timedelta(minutes=60):
            slots.append([iso(s), iso(e)])
    return slots
