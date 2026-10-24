from group_agent.planner import candidate_windows, parse_window_rules
from shared.config import iso
from tests.helpers import WED_NOON, at


def w(text, now=WED_NOON):
    r = parse_window_rules(text, now)
    return (iso(r[0]), iso(r[1])) if r else None


def test_tonight_tomorrow():
    assert w("anything tonight?") == ("2026-10-28T17:00:00-07:00", "2026-10-28T23:30:00-07:00")
    assert w("tomorrow?") == ("2026-10-29T17:00:00-07:00", "2026-10-29T23:30:00-07:00")
    assert w("tonight", at(2026, 10, 28, 22, 0)) is None   # under 2.5 h left -> LLM/default


def test_weekday_names():
    assert w("friday works") == ("2026-10-30T17:00:00-07:00", "2026-10-30T23:30:00-07:00")
    assert w("how about saturday") == ("2026-10-31T10:00:00-07:00", "2026-10-31T23:30:00-07:00")
    assert w("wednesday") == ("2026-10-28T17:00:00-07:00", "2026-10-28T23:30:00-07:00")


def test_this_weekend():
    assert w("plan something this weekend") == ("2026-10-30T17:00:00-07:00", "2026-11-01T22:00:00-08:00")
    # asked on Saturday: start from the next hour
    assert w("this weekend", at(2026, 10, 31, 12, 10)) == ("2026-10-31T13:00:00-07:00",
                                                          "2026-11-01T22:00:00-08:00")


def test_no_rule():
    assert w("sometime soon") is None


def _slots(*pairs):
    return [[iso(a), iso(b)] for a, b in pairs]


def test_candidate_windows_priority_and_non_overlap():
    ws, we = at(2026, 10, 30, 17, 0), at(2026, 10, 30, 23, 30)
    free = {
        "A": _slots((ws, we)),
        "B": _slots((ws, we)),
        "C": _slots((at(2026, 10, 30, 20, 0), we)),          # priority member, free late only
    }
    wins = candidate_windows(ws, we, free, priority=["C"])
    assert [x["start"][11:16] for x in wins] == ["20:00"]  # 21:00 overlaps 20:00
    assert wins[0]["free_members"] == ["A", "B", "C"]
    relaxed = candidate_windows(ws, we, free, priority=["Z"])   # unsatisfiable -> relax
    assert relaxed and relaxed[0]["start"][11:16] == "17:00"
