"""Live sources behind the fixture switch: any API failure falls back to fixtures."""

from datetime import timedelta

import httpx
import pytest

from group_agent.sources import events, weather
from group_agent.sources.places import Places
from shared.geo import geocode
from tests.helpers import at, run

S, E = at(2026, 10, 30, 17, 0), at(2026, 11, 1, 22, 0)


@pytest.fixture
def live_but_down(monkeypatch):
    monkeypatch.setenv("USE_FIXTURES", "0")
    monkeypatch.setenv("TICKETMASTER_API_KEY", "test-key")
    monkeypatch.setenv("PLACES_API_KEY", "test-key")

    async def boom(*a, **k):
        raise httpx.ConnectTimeout("simulated timeout")
    monkeypatch.setattr(httpx.AsyncClient, "get", boom)
    monkeypatch.setattr(httpx.AsyncClient, "post", boom)


def test_weather_timeout_uses_fixture(live_but_down):
    hours = run(weather.hourly(37.76, -122.41, S, E))
    sat = weather.summarize(hours, at(2026, 10, 31, 18, 0), at(2026, 10, 31, 20, 30))
    assert sat["rain_likely"] and sat["max_precip"] >= 50       # fixture: rainy Saturday
    assert not weather.summarize(hours, S, S + timedelta(hours=2.5))["rain_likely"]


def test_events_timeout_uses_fixture(live_but_down):
    evs = run(events.search(37.76, -122.41, S, E))
    names = {e["name"] for e in evs}
    assert "Friday Stand-up Showcase" in names and "Indie Night at The Chapel" in names
    assert all(S <= e["start"] <= E for e in evs)


def test_places_timeout_uses_fixture(live_but_down):
    p = Places()
    assert p.live is not None
    found = run(p.search("climbing gym", "climbing", 37.76, -122.41))
    assert found[0]["name"] == "Mission Cliffs"


def test_geocode_timeout_uses_fixture(live_but_down):
    assert run(geocode("Oakland")) == {"lat": 37.8, "lng": -122.27, "label": "Oakland"}


def test_ticketmaster_normalizer():
    raw = {"name": "Show", "dates": {"start": {"dateTime": "2026-10-31T03:00:00Z"}},
           "_embedded": {"venues": [{"name": "V", "location": {"latitude": "37.7", "longitude": "-122.4"}}]},
           "priceRanges": [{"min": 19.5}],
           "classifications": [{"segment": {"name": "Arts & Theatre"}, "genre": {"name": "Comedy"}}]}
    e = events._normalize(raw)
    assert e["start"].isoformat() == "2026-10-30T20:00:00-07:00"
    assert e["price_min"] == 19.5 and e["category"] == "comedy"
    assert events._normalize({"name": "broken"}) is None
