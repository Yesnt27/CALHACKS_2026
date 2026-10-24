import pytest

from group_agent.intents import classify_rules
from shared.cards import parse_selection


@pytest.mark.parametrize("text,intent", [
    ("join crew42 as Sam", "join"),
    ("plan something for the crew this weekend", "plan"),
    ("can we hang out friday?", "plan"),
    ("what's the plan", "vote_open"),
    ("vote", "vote_open"),
    ("lock it in", "lock"),
    ('recap: it was great "we did it" — Sam, we should vote again', "recap"),
    ("what did we do last summer", "lore_question"),
    ("anything from a year ago?", "lore_question"),
    ("help", "help"),
    ("confirm", "confirm"),
    ("cancel", "cancel"),
])
def test_rules(text, intent):
    assert classify_rules(text, parse_selection(text)) == intent


def test_card_selection_json_wins():
    sel = parse_selection('{"action": "vote", "plan_id": "p_41", "option_id": "opt_2"}')
    assert classify_rules("", sel) == "vote_select"


def test_prose_selection():
    assert parse_selection("vote opt_2 on p_41") == {"plan_id": "p_41", "option_id": "opt_2", "action": "vote"}


def test_unknown_goes_to_llm():
    assert classify_rules("hmm 🤔", {}) is None
