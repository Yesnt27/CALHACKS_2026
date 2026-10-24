from shared.geo import geocode, haversine_km, travel_minutes
from tests.helpers import run


def test_haversine_and_estimate():
    km = haversine_km(37.76, -122.42, 37.80, -122.27)   # Mission -> Oakland
    assert 13 < km < 15
    assert travel_minutes(37.76, -122.42, 37.76, -122.42) == 10
    assert travel_minutes(37.76, -122.42, 37.80, -122.27) == round(10 + km / 15 * 60)


def test_geocode_fixture_rounds_to_two_decimals():
    hit = run(geocode("Mission District"))
    assert hit == {"lat": 37.76, "lng": -122.42, "label": "Mission, SF"}
    assert run(geocode("Atlantis")) is None
