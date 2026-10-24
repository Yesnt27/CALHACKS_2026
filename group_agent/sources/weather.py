"""Open-Meteo hourly forecast with a fixture fallback."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from shared.config import TZ, TZ_NAME, use_fixtures

log = logging.getLogger("lore.weather")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FIXTURE = Path(__file__).parent / "fixtures" / "weather.json"


def _fixture_hourly(start: datetime, end: datetime) -> list[dict]:
    data = json.loads(FIXTURE.read_text())
    out = []
    t = start.replace(minute=0, second=0, microsecond=0)
    while t <= end:
        prof = data["by_weekday"].get(t.strftime("%A"), data["default"])
        out.append({"time": t, "precip": prof["precipitation_probability"][t.hour],
                    "temp": prof["temperature_2m"][t.hour]})
        t += timedelta(hours=1)
    return out


async def hourly(lat: float, lng: float, start: datetime, end: datetime) -> list[dict]:
    """[{time (aware), precip (%), temp (C)}] covering start..end."""
    if not use_fixtures():
        try:
            params = {"latitude": lat, "longitude": lng, "timezone": TZ_NAME,
                      "hourly": "precipitation_probability,temperature_2m",
                      "start_date": start.date().isoformat(), "end_date": end.date().isoformat()}
            async with httpx.AsyncClient(timeout=6) as client:
                r = await client.get(FORECAST_URL, params=params)
                r.raise_for_status()
                h = r.json()["hourly"]
            return [{"time": datetime.fromisoformat(t).replace(tzinfo=TZ), "precip": p or 0, "temp": c}
                    for t, p, c in zip(h["time"], h["precipitation_probability"], h["temperature_2m"])]
        except Exception as exc:
            log.warning("Open-Meteo failed (%s); using fixture", exc)
    return _fixture_hourly(start, end)


def summarize(hours: list[dict], start: datetime, end: datetime) -> dict:
    """{rain_likely, max_precip, temp_c, rain_from} for one window."""
    inside = [h for h in hours if start <= h["time"] < end] or hours[:1]
    if not inside:
        return {"rain_likely": False, "max_precip": 0, "temp_c": None, "rain_from": None}
    max_p = max(h["precip"] for h in inside)
    rain_from = next((h["time"] for h in inside if h["precip"] >= 50), None)
    temps = [h["temp"] for h in inside if h["temp"] is not None]
    return {"rain_likely": max_p >= 50, "max_precip": max_p,
            "temp_c": round(sum(temps) / len(temps)) if temps else None,
            "rain_from": rain_from.isoformat() if rain_from else None}
