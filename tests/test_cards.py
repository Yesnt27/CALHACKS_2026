import json

from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent
from uagents_core.contrib.protocols.chat.cards import (create_card_response_content, extract_card,
                                                       validate_card_payload)

from shared.cards import (card_message, card_of, carousel_item, carousel_payload, extract_selection,
                          quote_payload, review_payload, text_message)

OPTION = {"option_id": "opt_2", "title": "Night market + board game café", "subtitle": "Fri 7:00 pm · indoor",
          "secondary_text": "$ · max 22 min for anyone", "when": "Fri 7:00 pm", "place": "Chinatown",
          "badges": [{"label": "Works for everyone", "variant": "success"}]}


def test_payloads_validate_against_uagents_core_schemas():
    validate_card_payload("carousel", carousel_payload("Pick one", "This weekend",
                                                       [carousel_item(OPTION, "p_41")]))
    validate_card_payload("review", review_payload("p_41", OPTION, ["Alex", "Sam"]))
    validate_card_payload("custom", quote_payload("One year ago this week", "Bonfire · 5 of you",
                                                  [{"text": "exact", "speaker": "Sam"}]))


def test_card_message_wire_format_and_terminal_flag():
    msg = card_message("narration", "custom", quote_payload("t", "m", []), terminal=True)
    meta = msg.content[1].metadata
    assert msg.text() == "narration"
    assert meta["card_protocol_version"] == "1" and meta["is_terminal"] == "true"
    assert extract_card(MetadataContent(metadata=meta)).is_terminal
    assert json.loads(meta["card_payload"]) == card_of(msg)[1]


def test_invalid_payload_falls_back_to_text():
    msg = card_message("just text", "carousel", {"items": []})
    assert card_of(msg) is None and msg.text() == "just text"


def test_extract_selection_from_card_response_and_text():
    sel = {"action": "vote", "plan_id": "p_41", "option_id": "opt_2"}
    resp = ChatMessage(content=[create_card_response_content(selection=sel)])
    assert extract_selection(resp) == sel
    assert extract_selection(text_message(json.dumps(sel))) == sel
    assert extract_selection(text_message("vote for opt_2 please")) == {"option_id": "opt_2", "action": "vote"}
