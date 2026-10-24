from member_agent.calendar_ics import CalendarReader, busy_blocks, fetch_ics, free_slots
from tests.helpers import at, run

WS, WE = at(2026, 10, 30, 17, 0), at(2026, 11, 1, 22, 0)   # Fri 17:00 -> Sun 22:00 (DST ends Sun)


def test_recurring_events_expand_and_titles_are_dropped():
    raw = run(fetch_ics("fixture:busy_weeknights"))
    blocks = busy_blocks(raw, WS, WE)
    assert blocks == [(at(2026, 10, 30, 18, 30), at(2026, 10, 30, 21, 0)),
                      (at(2026, 10, 31, 12, 0), at(2026, 10, 31, 15, 0))]   # transparent all-day skipped
    assert all(len(b) == 2 for b in blocks)                # times only, no titles


def test_free_slots_subtract_busy_and_clip_to_latest_end():
    blocks = run(CalendarReader().busy("fixture:busy_weeknights", WS, WE))
    slots = free_slots(blocks, WS, WE, "22:47")
    assert slots[0] == ["2026-10-30T17:00:00-07:00", "2026-10-30T18:30:00-07:00"]
    assert slots[1] == ["2026-10-30T21:00:00-07:00", "2026-10-30T22:30:00-07:00"]   # 22:47 snapped down
    assert slots[-1] == ["2026-11-01T10:00:00-08:00", "2026-11-01T22:00:00-08:00"]  # offset after DST
    assert not any("22:47" in s for pair in slots for s in pair)


def test_no_calendar_defaults():
    slots = free_slots(None, WS, WE, None)
    assert slots == [["2026-10-30T17:00:00-07:00", "2026-10-30T23:00:00-07:00"],
                     ["2026-10-31T10:00:00-07:00", "2026-10-31T23:00:00-07:00"],
                     ["2026-11-01T10:00:00-08:00", "2026-11-01T22:00:00-08:00"]]


def test_unreadable_calendar_is_treated_as_none(tmp_path):
    bad = tmp_path / "bad.ics"
    bad.write_text("not a calendar")
    assert run(CalendarReader().busy(str(bad), WS, WE)) is None
