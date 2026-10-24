import inspect
from types import SimpleNamespace

import pytest

from shared.llm import LLM, build_pitch_input, parse_json, template_constraints
from tests.helpers import run


class FakeCompletions:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    async def create(self, **kw):
        self.calls += 1
        text = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))


def fake_llm(*replies):
    llm = LLM(api_key="")
    comp = FakeCompletions(replies)
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    return llm, comp


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": 2} hope that helps') == {"a": 2}


def test_bad_json_retries_once_then_template():
    llm, comp = fake_llm("nope", "still nope")
    assert run(llm.intent("hmm")) == "help"
    assert comp.calls == 2


def test_retry_recovers():
    llm, comp = fake_llm("nope", '{"intent": "plan"}')
    assert run(llm.intent("hmm")) == "plan" and comp.calls == 2


def test_ideas_validation_requires_novel_categories():
    bad = '{"ideas": [' + ",".join(['{"title": "k", "category": "karaoke"}'] * 8) + "]}"
    llm, comp = fake_llm(bad, bad)
    ideas = run(llm.ideas("Mission", [], [], {"karaoke": 3}, 5))
    assert len(ideas) == 8 and sum(i["category"] != "karaoke" for i in ideas) >= 4


def test_no_key_uses_templates():
    llm = LLM(api_key="")
    assert not llm.live
    assert run(llm.constraints("budget $25 and no alcohol")) == {"budget_cap_pp": 25.0, "no_alcohol": True}


def test_constraint_template_examples():
    out = template_constraints("Budget is $25, no alcohol, home by 11, max 30 min travel. I'm vegetarian.")
    assert out == {"budget_cap_pp": 25.0, "no_alcohol": True, "latest_end": "23:00",
                   "max_travel_min": 30, "other": "I'm vegetarian."}


# Privacy invariant 5 -----------------------------------------------------------

def test_build_pitch_input_signature():
    assert list(inspect.signature(build_pitch_input).parameters) == ["option_public", "weather",
                                                                       "novelty_note"]


@pytest.mark.parametrize("leak", ["free_members", "verdicts", "coming", "budget_cap_pp", "other"])
def test_build_pitch_input_rejects_non_public_fields(leak):
    with pytest.raises(ValueError):
        build_pitch_input({"option_id": "opt_1", "title": "x", leak: "secret"}, {}, "")


def test_build_pitch_input_rejects_extra_weather():
    with pytest.raises(ValueError):
        build_pitch_input({"option_id": "opt_1"}, {"rain_likely": True, "who_is_cold": "Sam"}, "")
