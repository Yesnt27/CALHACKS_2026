"""Venue lookup. Fixture provider by default; Google Places Text Search if PLACES_API_KEY is set."""

import json
import logging
import re
from pathlib import Path

import httpx

from shared.config import env, use_fixtures
from shared.geo import haversine_km

log = logging.getLogger("lore.places")

FIXTURE = Path(__file__).parent / "fixtures" / "places_sf.json"
TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PRICE_LEVELS = {"PRICE_LEVEL_FREE": "free", "PRICE_LEVEL_INEXPENSIVE": "$",
                "PRICE_LEVEL_MODERATE": "$$", "PRICE_LEVEL_EXPENSIVE": "$$$",
                "PRICE_LEVEL_VERY_EXPENSIVE": "$$$"}
INDOOR_HINTS = ("park", "beach", "trail", "market", "garden", "hike")


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", s.lower()) if len(t) > 2}


class FixturePlaces:
    def __init__(self, path: Path = FIXTURE):
        self.places = json.loads(path.read_text())

    async def search(self, query: str, category: str, lat: float, lng: float) -> list[dict]:
        q = _tokens(query)
        scored = []
        for p in self.places:
            words = _tokens(p["name"] + " " + " ".join(p["keywords"]))
            score = len(q & words) + (2 if category in p["categories"] else 0)
            if p["alcohol_centric"] and category != "bar" and not q & {"bar", "brewery", "beer"}:
                score -= 1  # prefer the non-bar venue unless a bar was asked for
            if score >= 2:
                dist = haversine_km(lat, lng, p["lat"], p["lng"])
                scored.append((-score, dist, p))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [dict(p) for _, _, p in scored[:3]]


class GooglePlaces:
    def __init__(self, key: str):
        self.key = key

    async def search(self, query: str, category: str, lat: float, lng: float) -> list[dict]:
        headers = {"X-Goog-Api-Key": self.key,
                   "X-Goog-FieldMask": "places.displayName,places.location,places.priceLevel,places.types"}
        body = {"textQuery": query, "maxResultCount": 3,
                "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng},
                                            "radius": 8000.0}}}
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.post(TEXT_SEARCH_URL, json=body, headers=headers)
            r.raise_for_status()
            found = r.json().get("places", [])
        out = []
        for p in found:
            types = " ".join(p.get("types", []))
            out.append({
                "name": p["displayName"]["text"],
                "lat": p["location"]["latitude"], "lng": p["location"]["longitude"],
                "categories": [category], "keywords": [],
                "price_band": PRICE_LEVELS.get(p.get("priceLevel", ""), "$$"),
                "indoor": not any(h in types for h in INDOOR_HINTS),
                "alcohol_centric": "bar" in p.get("types", []) or "night_club" in p.get("types", []),
            })
        return out


class Places:
    """Live provider with per-call fixture fallback."""

    def __init__(self):
        self.fixture = FixturePlaces()
        key = env("PLACES_API_KEY")
        self.live = GooglePlaces(key) if key and not use_fixtures() else None

    async def search(self, query: str, category: str, lat: float, lng: float) -> list[dict]:
        if self.live:
            try:
                found = await self.live.search(query, category, lat, lng)
                if found:
                    return found
            except Exception as exc:
                log.warning("Places search failed (%s); using fixture", exc)
        return await self.fixture.search(query, category, lat, lng)
