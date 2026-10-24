"""Distance, travel-time estimate and Open-Meteo geocoding."""

import logging
import math

import httpx

from shared.config import use_fixtures

log = logging.getLogger("lore.geo")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

# Offline fallback for common Bay Area neighborhoods (used with USE_FIXTURES=1
# or when the geocoder is unreachable).
FIXTURE_PLACES = {
    "mission": (37.76, -122.42, "Mission, SF"),
    "mission district": (37.76, -122.42, "Mission, SF"),
    "soma": (37.78, -122.40, "SoMa, SF"),
    "sunset": (37.75, -122.49, "Sunset, SF"),
    "inner sunset": (37.76, -122.47, "Inner Sunset, SF"),
    "richmond": (37.78, -122.48, "Richmond, SF"),
    "noe valley": (37.75, -122.43, "Noe Valley, SF"),
    "castro": (37.76, -122.44, "Castro, SF"),
    "haight": (37.77, -122.45, "Haight, SF"),
    "nob hill": (37.79, -122.41, "Nob Hill, SF"),
    "north beach": (37.80, -122.41, "North Beach, SF"),
    "marina": (37.80, -122.44, "Marina, SF"),
    "bernal heights": (37.74, -122.42, "Bernal Heights, SF"),
    "dogpatch": (37.76, -122.39, "Dogpatch, SF"),
    "san francisco": (37.77, -122.42, "San Francisco"),
    "sf": (37.77, -122.42, "San Francisco"),
    "oakland": (37.80, -122.27, "Oakland"),
    "berkeley": (37.87, -122.27, "Berkeley"),
    "daly city": (37.69, -122.47, "Daly City"),
    "san mateo": (37.56, -122.32, "San Mateo"),
    "palo alto": (37.44, -122.14, "Palo Alto"),
}


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def travel_minutes(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """ESTIMATE only: 10 min overhead + 15 km/h door-to-door. Not a transit API."""
    km = haversine_km(lat1, lng1, lat2, lng2)
    return round(10 + km / 15 * 60)


async def geocode(name: str) -> dict | None:
    """{lat, lng, label} rounded to 2 decimals, or None if not found."""
    key = name.strip().lower().removesuffix(", sf").removesuffix(" sf").strip()
    if not use_fixtures():
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                r = await client.get(GEOCODE_URL, params={"name": name, "count": 1})
                r.raise_for_status()
                results = r.json().get("results") or []
            if results:
                hit = results[0]
                label = ", ".join(x for x in (hit.get("name"), hit.get("admin1")) if x)
                return {"lat": round(hit["latitude"], 2), "lng": round(hit["longitude"], 2),
                        "label": label or name}
        except Exception as exc:
            log.warning("geocoding failed (%s); trying fixture table", exc)
    if key in FIXTURE_PLACES:
        lat, lng, label = FIXTURE_PLACES[key]
        return {"lat": round(lat, 2), "lng": round(lng, 2), "label": label}
    return None
