"""Every row of the verdict table, in order."""

from member_agent.profile import merge_profile, readback, verdict
from shared.protocol import OptionSpec
from tests.helpers import at

HOME = {"lat": 37.76, "lng": -122.42, "label": "Mission, SF"}   # Mission Cliffs is ~1 km away


def opt(**kw):
    base = dict(option_id="opt_1", start="2026-10-31T18:00:00-07:00", end="2026-10-31T20:30:00-07:00",
                cost_per_person=20.0, alcohol_centric=False, late_night=False,
                lat=37.7612, lng=-122.4125, indoor=True)
    return OptionSpec(**{**base, **kw})


def fit(o, profile=None, busy=(), home=HOME):
    return verdict(o, profile or {}, home, list(busy))[0]


def test_yes_by_default():
    assert fit(opt()) == "yes"


def test_no_alcohol_and_alcohol_centric_is_no():
    assert fit(opt(alcohol_centric=True), {"no_alcohol": True}) == "no"
    assert fit(opt(alcohol_centric=True), {"no_alcohol": False}) == "yes"


def test_overlapping_busy_block_is_no():
    assert fit(opt(), busy=[(at(2026, 10, 31, 20, 0), at(2026, 10, 31, 21, 0))]) == "no"
    assert fit(opt(), busy=[(at(2026, 10, 31, 20, 30), at(2026, 10, 31, 21, 0))]) == "yes"


def test_latest_end_plus_travel_is_no():
    # ends 20:30 + ~14 min travel; home by 20:40 -> no, home by 21:00 -> yes
    assert fit(opt(), {"latest_end": "20:40"}) == "no"
    assert fit(opt(), {"latest_end": "21:00"}) == "yes"


def test_small_hours_latest_end_is_next_day():
    assert fit(opt(end="2026-10-31T23:30:00-07:00"), {"latest_end": "01:00"}) == "yes"


def test_cost_over_120pct_cap_is_no():
    assert fit(opt(cost_per_person=25.0), {"budget_cap_pp": 20}) == "no"      # 25 > 24
    assert fit(opt(cost_per_person=24.0), {"budget_cap_pp": 20}) == "stretch"


def test_travel_over_150pct_max_is_no():
    far = opt(lat=37.8094, lng=-122.4166)   # Musee Mecanique, ~5.5 km -> ~32 min
    assert fit(far, {"max_travel_min": 20}) == "no"       # 32 > 30
    assert fit(far, {"max_travel_min": 25}) == "stretch"  # 25 < 32 <= 37.5


def test_over_cap_or_over_travel_is_stretch():
    assert fit(opt(cost_per_person=21.0), {"budget_cap_pp": 20}) == "stretch"
    assert fit(opt(), {"max_travel_min": 10}) == "stretch"   # ~13 min: > 10, <= 15
    assert fit(opt(), {"max_travel_min": 8}) == "no"         # ~13 min > 12


def test_rule_order_alcohol_before_cost():
    assert verdict(opt(alcohol_centric=True, cost_per_person=999), {"no_alcohol": True, "budget_cap_pp": 1},
                   HOME, [])[2] == "alcohol-centric"


def test_returns_travel_minutes_estimate():
    _, travel, _ = verdict(opt(), {}, HOME, [])
    assert isinstance(travel, int) and 10 <= travel <= 20


def test_merge_and_readback():
    p = merge_profile({}, {"budget_cap_pp": 25.0, "no_alcohol": True, "latest_end": "23:00",
                           "max_travel_min": 30, "other": "vegetarian"})
    text = readback(p)
    assert text.startswith("Here's what I'll keep private:")
    assert "$25/person" in text and "11:00 pm" in text and "30 min" in text and "fix:" in text
    assert merge_profile(p, {"budget_cap_pp": None})["budget_cap_pp"] is None
