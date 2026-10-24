"""Privacy invariant 1: member replies carry only ids, ISO times, enums, ints and bools."""

from shared.protocol import AvailabilityReply, EvaluateReply, Verdict


def _fields(model):
    return {name: f.outer_type_ for name, f in model.__fields__.items()}


def test_verdict_fields_exact():
    assert _fields(Verdict) == {"option_id": str, "fit": str, "travel_min": int}


def test_evaluate_reply_fields_exact():
    f = _fields(EvaluateReply)
    assert set(f) == {"plan_id", "verdicts"}
    assert f["plan_id"] is str
    assert f["verdicts"] == list[Verdict]


def test_availability_reply_fields_exact():
    f = _fields(AvailabilityReply)
    assert set(f) == {"plan_id", "free_slots", "has_calendar"}
    assert f["plan_id"] is str and f["has_calendar"] is bool
    assert f["free_slots"] == list[list[str]]
