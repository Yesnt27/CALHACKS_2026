"""Ticketmaster Discovery v2 events with a fixture fallback."""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from shared.config import TZ, env, use_fixtures

log = logging.getLogger("lore.events")

DISCOVERY_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
FIXTURE = Path(__file__).parent / "fixtures" / "events.json"


def _category(segment: str, genre: str) -> str:
    seg, gen = (segment or "").lower(), (genre or "").lower()
    if "comedy" in gen:
        return "comedy"
    if seg == "music":
        return "live_music"
    if seg == "sports":
        return "sports"
    if seg == "film":
        return "movies"
    return "comedy" if "theatre" in seg else "live_music"


def _normalize(e: dict) -> dict | None:
    """Ticketmaster event -> {name, start, place, lat, lng, price_min, category}."""
    try:
        venue = e["_embedded"]["venues"][0]
        start = e["dates"]["start"]
        if start.get("dateTime"):
            when = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00")).astimezone(TZ)
        else:
            when = datetime.fromisoformat(f"{start['localDate']}T{start.get('localTime', '19:00:00')}"
                                          ).replace(tzinfo=TZ)
        cls = (e.get("classifications") or [{}])[0]
        prices = e.get("priceRanges") or []
        return {
            "name": e["name"], "start": when, "place": venue.get("name", ""),
            "lat": float(venue["location"]["latitude"]), "lng": float(venue["location"]["longitude"]),
            "price_min": float(prices[0]["min"]) if prices and prices[0].get("min") is not None else None,
            "category": _category(cls.get("segment", {}).get("name", ""),
                                  cls.get("genre", {}).get("name", "")),
        }
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def _fixture_raw(start: datetime, end: datetime) -> list[dict]:
    """Place fixture events on matching weekdays inside the window, in Ticketmaster shape."""
    raw = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    for ev in json.loads(FIXTURE.read_text())["events"]:
        d = day
        while d <= end:
            if d.strftime("%A") == ev["weekday"]:
                hh, mm = map(int, ev["local_time"].split(":"))
                when = d.replace(hour=hh, minute=mm)
                if start <= when <= end:
                    raw.append({
                        "name": ev["name"],
                        "dates": {"start": {"dateTime": when.astimezone(timezone.utc)
                                            .strftime("%Y-%m-%dT%H:%M:%SZ")}},
                        "_embedded": {"venues": [{"name": ev["venue"]["name"], "location": {
                            "latitude": str(ev["venue"]["lat"]), "longitude": str(ev["venue"]["lng"])}}]},
                        "priceRanges": [{"min": ev["price_min"]}] if ev.get("price_min") is not None else [],
                        "classifications": [{"segment": {"name": ev["segment"]},
                                             "genre": {"name": ev["genre"]}}],
                    })
            d += timedelta(days=1)
    return raw


async def search(lat: float, lng: float, start: datetime, end: datetime) -> list[dict]:
    key = env("TICKETMASTER_API_KEY")
    raw: list[dict] | None = None
    if key and not use_fixtures():
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            params = {"apikey": key, "latlong": f"{lat},{lng}", "radius": 10, "unit": "miles",
                      "startDateTime": start.astimezone(timezone.utc).strftime(fmt),
                      "endDateTime": end.astimezone(timezone.utc).strftime(fmt), "size": 20}
            async with httpx.AsyncClient(timeout=6) as client:
                r = await client.get(DISCOVERY_URL, params=params)
                r.raise_for_status()
                raw = r.json().get("_embedded", {}).get("events", [])
        except Exception as exc:
            log.warning("Ticketmaster failed (%s); using fixture", exc)
    if raw is None:
        raw = _fixture_raw(start, end)
    return [e for e in (_normalize(x) for x in raw) if e]
