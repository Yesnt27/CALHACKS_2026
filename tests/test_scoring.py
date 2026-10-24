from group_agent.scoring import novelty, score, score_candidates, top_distinct, weather_fit


def test_novelty_and_weather():
    assert novelty("climbing", {"karaoke": 3}) == 1.5
    assert novelty("karaoke", {"karaoke": 3}) == -1.5
    assert weather_fit(True, True) == 1 and weather_fit(False, True) == -2
    assert weather_fit(False, False) == 0.5 and weather_fit(True, False) == 0


def test_score_formula():
    s = score(avail_frac=0.8, priority_all_free=True, novelty_pts=1.5, weather_pts=1,
              stretch_count=2, worst_travel_min=45)
    assert s == 3 * 0.8 + 2 + 1.5 + 1 - 1 - 2


def _cand(oid, cat, window=0, indoor=True, free=("A", "B")):
    return {"option_id": oid, "category": cat, "window": window, "indoor": indoor,
            "free_members": list(free), "start": f"2026-10-30T{17 + window}:00:00-07:00"}


def test_any_no_drops_option_and_aggregates_only():
    cands = [_cand("opt_1", "climbing"), _cand("opt_2", "karaoke"), _cand("opt_3", "arcade")]
    verdicts = {"A": {"opt_1": {"fit": "yes", "travel_min": 12}, "opt_2": {"fit": "no", "travel_min": 9},
                      "opt_3": {"fit": "stretch", "travel_min": 40}},
                "B": {"opt_1": {"fit": "yes", "travel_min": 20}, "opt_2": {"fit": "yes", "travel_min": 9},
                      "opt_3": {"fit": "yes", "travel_min": 15}}}
    out = score_candidates(cands, verdicts, 2, [], {"karaoke": 3}, [{"rain_likely": True}])
    assert [o["option_id"] for o in out] == ["opt_1", "opt_3"]
    assert out[0]["all_yes"] and not out[1]["all_yes"]
    assert out[1]["stretch_count"] == 1 and out[1]["worst_travel_min"] == 40


def test_top_distinct_categories():
    scored = [dict(_cand(f"opt_{i}", c), score=10 - i) for i, c in
              enumerate(["climbing", "climbing", "arcade", "museum", "comedy"])]
    assert [c["category"] for c in top_distinct(scored)] == ["climbing", "arcade", "museum"]
