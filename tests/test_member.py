"""Owner-only chat and group-only protocol (privacy invariant 7), plus member commands."""

from member_agent.brain import NOT_YOURS, MemberBrain
from shared.llm import LLM
from shared.protocol import AvailabilityRequest, EvaluateRequest, OptionSpec
from tests.helpers import run

GROUP = "agent1q-group"


def brain(tmp_path):
    return MemberBrain("Alex", tmp_path / "alex.json", GROUP, LLM(api_key=""))


def paired(tmp_path):
    b = brain(tmp_path)
    run(b.on_chat("owner", f"pair {b.state['pair_code']}"))
    return b


def test_pairing_first_correct_code_wins(tmp_path):
    b = brain(tmp_path)
    code = b.state["pair_code"]
    assert len(code) == 6
    assert run(b.on_chat("stranger", "pair WRONG1")) == [NOT_YOURS]
    assert run(b.on_chat("stranger", "hello")) == [NOT_YOURS]
    assert run(b.on_chat("owner", f"pair {code.lower()}"))[0].startswith("Paired")
    assert run(b.on_chat("someone-else", f"pair {code}")) == [NOT_YOURS]
    assert b.state["owner_sender"] == "owner"


def test_non_owner_gets_nothing_else(tmp_path):
    b = paired(tmp_path)
    for text in ("profile", "budget $5", "forget", "calendar fixture:busy_weeknights", "help"):
        assert run(b.on_chat("intruder", text)) == [NOT_YOURS]
    assert b.state["profile"]["budget_cap_pp"] is None


def test_protocol_only_from_group(tmp_path):
    b = paired(tmp_path)
    req = AvailabilityRequest(plan_id="p_1", window_start="2026-10-30T17:00:00-07:00",
                              window_end="2026-10-30T23:30:00-07:00")
    assert run(b.on_availability("agent1q-evil", req)) is None
    assert run(b.on_availability(GROUP, req)) is not None
    opt = OptionSpec(option_id="opt_1", start="2026-10-30T18:00:00-07:00", end="2026-10-30T20:00:00-07:00",
                     cost_per_person=10, alcohol_centric=False, late_night=False, lat=37.76, lng=-122.42,
                     indoor=True)
    assert run(b.on_evaluate("agent1q-evil", EvaluateRequest(plan_id="p_1", options=[opt]))) is None
    reply = run(b.on_evaluate(GROUP, EvaluateRequest(plan_id="p_1", options=[opt])))
    assert [v.fit for v in reply.verdicts] == ["yes"]


def test_empty_group_address_rejects_all(tmp_path):
    b = MemberBrain("Alex", tmp_path / "a.json", "", LLM(api_key=""))
    req = AvailabilityRequest(plan_id="p", window_start="2026-10-30T17:00:00-07:00",
                              window_end="2026-10-30T23:30:00-07:00")
    assert run(b.on_availability("", req)) is None


def test_constraints_readback_fix_profile_forget(tmp_path):
    b = paired(tmp_path)
    out = run(b.on_chat("owner", "budget $25, no alcohol, home by 11, max 30 min travel. I'm vegetarian"))[0]
    assert out.startswith("Here's what I'll keep private:") and "fix:" in out
    out = run(b.on_chat("owner", "fix: budget $40"))[0]
    assert "$40/person" in out and "no alcohol" in out
    assert "$40/person" in run(b.on_chat("owner", "profile"))[0]
    run(b.on_chat("owner", "forget"))
    assert b.state["profile"]["budget_cap_pp"] is None and b.state["home"] is None


def test_calendar_and_home_commands(tmp_path):
    b = paired(tmp_path)
    assert run(b.on_chat("owner", "calendar fixture:busy_weeknights")) == [
        "Got it. I only read busy times, never event titles."]
    assert run(b.on_chat("owner", "calendar fixture:does_not_exist"))[0].startswith("I couldn't read")
    assert run(b.on_chat("owner", "home Noe Valley"))[0].startswith("Home area set to Noe Valley")
    assert b.state["home"] == {"lat": 37.75, "lng": -122.43, "label": "Noe Valley, SF"}
    reply = run(b.on_availability(GROUP, AvailabilityRequest(
        plan_id="p_2", window_start="2026-10-30T17:00:00-07:00", window_end="2026-10-30T23:30:00-07:00")))
    assert reply.has_calendar and reply.free_slots[0] == ["2026-10-30T17:00:00-07:00",
                                                          "2026-10-30T18:30:00-07:00"]


def test_state_persists_across_restart(tmp_path):
    b = paired(tmp_path)
    run(b.on_chat("owner", "budget $30"))
    again = brain(tmp_path)
    assert again.state["owner_sender"] == "owner" and again.state["profile"]["budget_cap_pp"] == 30.0
