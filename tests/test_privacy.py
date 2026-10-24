"""Privacy invariants 2, 3, 4, 6 and 8, run against full in-process plans."""

import json
import re

from group_agent.brain import extract_quotes
from scripts.simulate_plan import PROFILES, Simulation
from shared.cards import card_of
from shared.protocol import EvaluateReply
from tests.helpers import WED_NOON, as_dict, run, walk

SENTINEL = "SENTINEL"
NUMS = {987.65, 913, 37.71, -122.46}
TEXT_RE = re.compile(r"SENTINEL|\b987\.65\b|\b913\b|23:41|-122\.46\b|\b37\.71\b")

SENTINEL_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//t//EN
BEGIN:VEVENT
UID:s1@t
DTSTART;TZID=America/Los_Angeles:20261031T120000
DTEND;TZID=America/Los_Angeles:20261031T130000
SUMMARY:SENTINEL-TITLE dentist
LOCATION:SENTINEL-LOCATION
DESCRIPTION:SENTINEL-DESCRIPTION
END:VEVENT
END:VCALENDAR
"""


def sentinel_profiles(tmp_path):
    ics = tmp_path / "SENTINEL-calendar.ics"
    ics.write_text(SENTINEL_ICS)
    prof = {"budget_cap_pp": 987.65, "no_alcohol": True, "latest_end": "23:41",
            "max_travel_min": 913, "other": "SENTINEL-OTHER allergic to cats"}
    return {name: {"profile": dict(prof),
                   "home": {"lat": 37.71, "lng": -122.46, "label": f"SENTINEL-HOME-{name}"},
                   "ical_url": str(ics)} for name in PROFILES}


def full_plan(sim):
    run(sim.say("user:alex", "plan something for the crew this weekend"))
    plan = run(sim.run_until("voting"))
    assert plan is not None, "plan should reach voting"
    opt = plan["options"][0]["option_id"]
    for u in ("user:sam", "user:jordan", "user:priya"):
        run(sim.say(u, selection={"action": "vote", "plan_id": plan["id"], "option_id": opt}))
    run(sim.say("user:alex", "lock it in"))
    run(sim.say("user:alex", selection={"action": "confirm", "plan_id": plan["id"]}))
    return plan


def test_invariant2_no_profile_value_in_any_message_or_prompt(tmp_path):
    prompts = []
    sim = Simulation(WED_NOON, profiles=sentinel_profiles(tmp_path), tmp=tmp_path)
    sim.llm.on_prompt(lambda purpose, messages: prompts.append((purpose, messages)))
    full_plan(sim)
    run(sim.say("user:riley", "what did we do a year ago?"))
    assert prompts, "LLM prompts should have been built"
    assert len(sim.sent) > 20
    for src, dst, msg in sim.sent:
        for leaf in walk(as_dict(msg)):
            if isinstance(leaf, str):
                assert not TEXT_RE.search(leaf), f"leak in {type(msg).__name__} {src}->{dst}: {leaf!r}"
            elif isinstance(leaf, (int, float)) and not isinstance(leaf, bool):
                assert leaf not in NUMS, f"numeric leak {leaf} in {type(msg).__name__}"
    for purpose, messages in prompts:
        blob = json.dumps(messages, ensure_ascii=False)
        assert not TEXT_RE.search(blob), f"leak in {purpose} prompt"


def _cards(sim):
    for msgs in sim.inbox.values():
        for m in msgs:
            if c := card_of(m):
                yield m, c


def test_invariant3_no_option_with_a_no_ever_appears_in_a_card(tmp_path):
    profiles = {k: dict(v) for k, v in PROFILES.items()}
    profiles["Priya"] = {**PROFILES["Priya"], "profile": {"budget_cap_pp": 15}}   # vetoes $$ options
    sim = Simulation(WED_NOON, profiles=profiles, tmp=tmp_path)
    full_plan(sim)
    vetoed = {v.option_id for _, _, m in sim.sent if isinstance(m, EvaluateReply)
              for v in m.verdicts if v.fit == "no"}
    assert vetoed, "this setup should produce some 'no' verdicts"
    plan = sim.db.plans_in("locked")[-1]
    titles_vetoed = {c["title"] for c in plan.get("work", {}).get("candidates", [])
                     if c["option_id"] in vetoed}
    for msg, (kind, payload) in _cards(sim):
        ids = {leaf for leaf in walk(payload) if isinstance(leaf, str) and leaf.startswith("opt_")}
        assert not ids & vetoed, f"vetoed option shown in {kind} card"
        assert not any(t in msg.text() for t in titles_vetoed if t)


BADGE_OK = re.compile(r"^(Works for everyone|New for you|You've done this \d+×|"
                      r"(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day works for \d+/\d+)$")


def test_invariant4_no_per_member_verdicts_in_cards(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    full_plan(sim)
    names = {b.name for b in sim.members.values()}
    seen = 0
    for msg, (kind, payload) in _cards(sim):
        seen += 1
        for item in payload.get("items", []):
            for b in item.get("badges", []):
                assert BADGE_OK.match(b["label"]), b["label"]
        for leaf in walk(payload):
            if isinstance(leaf, str):
                assert not re.search(r"\bstretch\b|\bverdict|\breason", leaf, re.I), leaf
        if kind == "review":   # names only as "who's coming" (time availability)
            rows = {r["label"]: r["value"] for r in payload["summary_rows"]}
            for label, value in rows.items():
                if label != "Who's coming":
                    assert not any(n in value for n in names), (label, value)
    assert seen >= 2


def test_invariant6_recap_quotes_are_byte_for_byte(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    full_plan(sim)
    sim.clock.advance(4 * 24 * 3600)   # recap after the hangout happened
    quotes = ['it\'s giving "main character" energy', "I am never doing that again 😂",
              "rematch — next week, same time 🎳✨"]
    text = (f"recap: wild night. “{quotes[0]}” — Sam. Jordan said \"{quotes[1]}\" "
            f"and “{quotes[2]}”, said Priya. with Alex, Sam and Jordan")
    run(sim.say("user:alex", text))
    h = sim.db.hangouts(limit=1)[0]
    assert h["title"] == sim.db.plans_in("locked")[-1]["options"][0]["title"]
    stored = [q["text"] for q in h["quotes"]]
    assert stored == quotes
    assert [s.encode("utf-8") for s in stored] == [q.encode("utf-8") for q in quotes]
    assert [q["speaker"] for q in h["quotes"]] == ["Sam", "Jordan", "Priya"]
    assert h["status"] == "done" and h["attendees"] == ["Alex", "Sam", "Jordan"]
    for q in quotes:
        assert q in h["recap"]


def test_extract_quotes_placeholders():
    body, quotes, spans = extract_quotes('a "b c" d “e”')
    assert body == "a [[Q1]] d [[Q2]]" and [q["text"] for q in quotes] == ["b c", "e"]
    assert spans == ['"b c"', "“e”"]


def test_invariant8_plan_replies_purged_and_lore_has_no_verdicts(tmp_path):
    sim = Simulation(WED_NOON, tmp=tmp_path)
    plan = full_plan(sim)
    assert sim.db.count_replies(plan["id"]) == 0
    assert sim.db.plan(plan["id"])["work_json"] is None
    run(sim.say("user:alex", "plan something tomorrow"))
    second = run(sim.run_until("voting"))
    assert sim.db.count_replies(second["id"]) > 0
    run(sim.say("user:alex", "cancel"))
    assert sim.db.count_replies(second["id"]) == 0 and sim.db.count_replies() == 0
    cols = {r[1] for r in sim.db.conn.execute("PRAGMA table_info(hangouts)")}
    assert cols == {"id", "date", "title", "categories", "place", "attendees", "recap", "quotes", "status"}
    dump = json.dumps(sim.db.hangouts(limit=100), ensure_ascii=False)
    assert not re.search(r"\b(stretch|travel_min|fit)\b", dump)
