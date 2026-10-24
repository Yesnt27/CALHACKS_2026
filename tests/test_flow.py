"""End-to-end flows through the Group brain (M3/M4 acceptance in-process)."""

from types import SimpleNamespace

from scripts.simulate_plan import INVITE, Simulation
from shared.cards import card_of, text_message
from tests.helpers import WED_NOON, run
from tests.test_llm import FakeCompletions


def plan_to_voting(sim, ask="plan something for the crew this weekend"):
    run(sim.say("user:alex", ask))
    return run(sim.run_until("voting"))


def test_join_flow_and_gating(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    out = run(sim.say("asi:newcomer", "plan something tonight"))
    assert "join" in out[0].text()
    assert "doesn't match" in run(sim.say("asi:newcomer", "join wrong as Sam"))[0].text()
    assert "don't have a member" in run(sim.say("asi:newcomer", f"join {INVITE} as Zed"))[0].text()
    assert run(sim.say("asi:newcomer", f"join {INVITE} as sam"))[0].text().startswith("Welcome, Sam")
    assert sim.db.member_for_sender("asi:newcomer") == "Sam"
    assert sim.db.member_for_sender("user:sam") is None   # rebinding moves the sender


def test_full_loop_three_voters_review_lock(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    plan = plan_to_voting(sim)
    final = sim.inbox["user:alex"][-1]
    kind, payload = card_of(final)
    assert kind == "carousel" and len(payload["items"]) == 3
    assert len({i["title"] for i in payload["items"]}) == 3
    opt = payload["items"][0]["primary_cta"]["selection"]
    assert run(sim.say("user:sam", selection=opt))[0].text() == "Vote counted (1/5 voted)."
    assert run(sim.say("user:jordan", "vote 1"))[0].text() == "Vote counted (2/5 voted)."
    out = run(sim.say("user:priya", f"vote {opt['option_id']}"))[0].text()
    assert out.startswith("Vote counted (3/5 voted).") and "majority" in out
    # planner's next interaction gets the review card
    replies = run(sim.say("user:alex", "what's the plan"))
    review = [card_of(m) for m in replies if card_of(m) and card_of(m)[0] == "review"]
    assert review and review[0][1]["approve_cta"]["selection"] == {"action": "confirm", "plan_id": plan["id"]}
    done = run(sim.say("user:alex", selection=review[0][1]["approve_cta"]["selection"]))
    assert done[0].text().startswith("Locked in:")
    p = sim.db.plan(plan["id"])
    assert p["state"] == "locked" and p["chosen_option"] == opt["option_id"]
    h = sim.db.hangouts("status='planned'", limit=1)[0]
    assert h["title"] == payload["items"][0]["title"] and h["date"] == p["options"][0]["start"]


def test_novelty_and_priority_visible(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    assert sim.db.priority_members() == ["Riley"]
    assert sim.db.category_counts()["karaoke"] == 3
    plan = plan_to_voting(sim)
    assert all("Riley" in w["free_members"] for w in plan["work"]["windows"])
    labels = {b["label"] for o in plan["options"] for b in o["badges"]}
    assert "New for you" in labels


def test_repeat_category_badge(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    for i in range(3):
        sim.db.add_hangout(f"2026-10-2{i}", "Climb", ["climbing"], "Mission Cliffs", ["Alex"])
    sim.db.conn.execute("UPDATE hangouts SET categories='[\"climbing\"]'")   # everything was climbing
    sim.db.conn.commit()
    plan = plan_to_voting(sim)
    labels = [b["label"] for o in plan["options"] for b in o["badges"]]
    assert any(label.startswith("You've done this") for label in labels) or \
        all(o["category"] != "climbing" for o in plan["options"])


def test_offline_member_counts_as_unknown(tmp_path):
    sim = Simulation(WED_NOON, offline={"Sam"}, tmp=tmp_path)
    plan = plan_to_voting(sim)
    assert plan is not None
    assert "Sam" not in plan["work"]["responded"]
    assert all("Sam" not in w["free_members"] for w in plan["work"]["windows"])
    labels = {b["label"] for o in plan["options"] for b in o["badges"]}
    assert any("works for 4/5" in label for label in labels)
    assert "Works for everyone" not in labels


def test_everyone_offline_gives_up_politely(tmp_path):
    sim = Simulation(WED_NOON, offline={"Alex", "Sam", "Jordan", "Priya", "Riley"}, tmp=tmp_path)
    assert plan_to_voting(sim) is None
    assert "couldn't reach" in sim.inbox["user:alex"][-1].text()
    assert sim.db.plans_in("cancelled")


def test_unreadable_selection_resends_card_with_hint(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    plan = plan_to_voting(sim)
    out = run(sim.say("user:sam", selection={"action": "vote", "plan_id": plan["id"], "option_id": "opt_999"}))
    assert out[0].text().startswith("I couldn't read that vote") and card_of(out[0])[0] == "carousel"
    out = run(sim.say("user:sam", "vote 7"))
    assert out[0].text().startswith("I couldn't read that vote")


def test_vote_before_options_ready(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    run(sim.say("user:alex", "plan something this weekend"))
    assert "Still gathering" in run(sim.say("user:sam", "what's the plan"))[0].text()


def test_new_plan_supersedes_open_vote(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    first = plan_to_voting(sim)
    out = run(sim.say("user:alex", "plan something tomorrow"))
    assert "closed the previous vote" in out[0].text()
    assert sim.db.plan(first["id"])["state"] == "cancelled"


def test_lore_question_quote_card_is_terminal_and_verbatim(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    out = run(sim.say("user:riley", "anything from a year ago?"))
    kind, payload = card_of(out[0])
    meta = out[0].content[1].metadata
    assert kind == "custom" and meta["is_terminal"] == "true"
    assert payload["root"]["title"] == "One year ago this week"
    values = [c["value"] for c in payload["root"]["children"]]
    assert "“this is the best night of the year, I'm calling it” — Sam" in values
    summer = run(sim.say("user:riley", "what did we do last summer?"))[0]
    assert "Dolores Park picnic" in summer.text()
    children = card_of(summer)[1]["root"]["children"]
    assert children[0]["value"] == "Escape room (we lost) · 4 of you"      # most recent quoted one
    assert children[1]["value"] == "“the lock was rigged” — Sam"


def test_llm_json_failure_falls_back_to_templates(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    comp = FakeCompletions(["not json"] * 50)
    sim.llm.client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    plan = plan_to_voting(sim)
    assert plan is not None and len(plan["options"]) == 3
    assert comp.calls >= 4          # IDEAS and PITCH each tried twice


def test_push_disabled_by_default_and_enabled(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    plan_to_voting(sim)
    assert "user:sam" not in sim.inbox
    sim2 = Simulation(WED_NOON, tmp=tmp_path / "p", push=True)
    plan_to_voting(sim2)
    assert card_of(sim2.inbox["user:sam"][-1])[0] == "carousel"


def test_help_works_for_anyone(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    assert "Lore Keeper" in run(sim.say("asi:stranger", "help"))[0].text()
    run(sim.group.on_chat("asi:stranger", text_message(""), sim.send))   # empty message: ignored


def test_llm_window_when_no_rule_matches(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    comp = FakeCompletions(['{"start": "2026-11-03T17:00:00-08:00", "end": "2026-11-04T23:30:00-08:00"}']
                           + ["not json"] * 20)
    sim.llm.client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    run(sim.say("user:alex", "plan something early next week"))
    plan = sim.db.current_plan()
    assert plan["window_start"] == "2026-11-03T17:00:00-08:00"
    assert plan["work"]["window_label"] == "Tue Nov 3 – Wed Nov 4"


def test_unparseable_window_defaults_to_this_weekend(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    run(sim.say("user:alex", "plan something soonish"))
    assert sim.db.current_plan()["window_start"] == "2026-10-30T17:00:00-07:00"


def test_lore_keyword_and_last_month(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    out = run(sim.say("user:sam", "how many times have we done karaoke?"))[0].text()
    assert "Karaoke round 3" in out and "Holiday karaoke" in out
    out = run(sim.say("user:sam", "what did we do last month?"))[0].text()
    assert "Karaoke round 3" in out and "Board game night" in out and "Bowling" not in out


def test_prose_confirm_and_cancel(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    plan = plan_to_voting(sim)
    run(sim.say("user:sam", "vote 2"))
    run(sim.say("user:alex", "lock it in"))
    assert run(sim.say("user:alex", "confirm"))[0].text().startswith("Locked in: " + plan["options"][1]["title"])
    plan2 = plan_to_voting(sim, "plan something friday")
    assert run(sim.say("user:alex", "cancel"))[0].text().startswith("Cancelled")
    assert sim.db.plan(plan2["id"])["state"] == "cancelled"
