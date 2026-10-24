"""ChatMessage / ASI:One card builders, selection parsing and the ack helper.

All user-visible card layout lives in this file so the look can be restyled in
one place. Layout is intentionally plain for now.
"""

import json
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    TextContent,
)
from uagents_core.contrib.protocols.chat.cards import (
    extract_card_response,
    validate_card_payload,
)

log = logging.getLogger("lore.cards")

MAX_PAYLOAD_BYTES = 64 * 1024


def text_message(text: str) -> ChatMessage:
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                       content=[TextContent(type="text", text=text)])


def card_message(narration: str, card_kind: str, payload: dict,
                 terminal: bool = False) -> ChatMessage:
    """Narration + card. If the payload fails validation, send the narration alone."""
    raw = json.dumps(payload, ensure_ascii=False)
    try:
        validate_card_payload(card_kind, payload)
        if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"card_payload is {len(raw)} bytes (> 64 KB)")
    except Exception as exc:  # pydantic ValidationError or ValueError
        log.warning("card %s failed validation, sending text only: %s", card_kind, exc)
        return text_message(narration)
    meta = {  # wire type is dict[str, str]
        "card_protocol_version": "1",
        "requires_card_interaction": "true",
        "card_kind": card_kind,            # carousel | detail | form | review | custom
        "card_id": str(uuid4()),
        "card_payload": raw,
        "preferred_drawer_width_px": "540",
    }
    if terminal:
        meta["is_terminal"] = "true"       # informational card, no input expected
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                       content=[TextContent(type="text", text=narration),
                                MetadataContent(type="metadata", metadata=meta)])


def card_of(msg: ChatMessage) -> tuple[str, dict] | None:
    """(card_kind, payload) of the first card in a message, for tests and logging."""
    for block in msg.content:
        if isinstance(block, MetadataContent) and "card_kind" in block.metadata:
            return block.metadata["card_kind"], json.loads(block.metadata["card_payload"])
    return None


async def ack(ctx, sender: str, msg: ChatMessage) -> None:
    await ctx.send(sender, ChatAcknowledgement(
        timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))


def parse_selection(text: str) -> dict:
    """Direct @mention delivers selection JSON; planner-routed chats deliver prose."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (ValueError, TypeError):
        pass
    sel: dict = {}
    if m := re.search(r"\bp_[A-Za-z0-9]+\b", text): sel["plan_id"] = m.group(0)
    if m := re.search(r"\bopt_[A-Za-z0-9]+\b", text): sel["option_id"] = m.group(0)
    low = text.lower()
    for action in ("vote", "confirm", "cancel", "lock"):
        if re.search(rf"\b{action}\b", low):
            sel.setdefault("action", action)
    return sel


def extract_selection(msg: ChatMessage) -> dict:
    """Card response metadata first (standard path), then text JSON / prose."""
    for block in msg.content:
        if isinstance(block, MetadataContent):
            resp = extract_card_response(block)
            if resp is not None and resp.selection:
                return dict(resp.selection)
    return parse_selection(msg.text().strip())


# ---------------------------------------------------------------- payloads

def carousel_payload(title: str, subtitle: str | None, items: list[dict]) -> dict:
    payload: dict = {"title": title, "items": items}
    if subtitle:
        payload["subtitle"] = subtitle
    return payload


def carousel_item(option: dict, plan_id: str) -> dict:
    """One voting card per option. `option` is the public option dict stored on the plan."""
    item = {
        "id": option["option_id"],
        "title": option["title"],
        "subtitle": option["subtitle"],
        "secondary_text": option["secondary_text"],
        "primary_cta": {"label": "Vote",
                        "selection": {"action": "vote", "plan_id": plan_id,
                                      "option_id": option["option_id"]}},
    }
    if option.get("badges"):
        item["badges"] = option["badges"]
    return item


def review_payload(plan_id: str, option: dict, coming: list[str]) -> dict:
    return {
        "title": "Lock it in?",
        "summary_rows": [
            {"label": "Plan", "value": option["title"]},
            {"label": "When", "value": option["when"]},
            {"label": "Where", "value": option["place"]},
            {"label": "Who's coming", "value": ", ".join(coming) or "TBD"},
        ],
        "approve_cta": {"label": "Confirm", "primary": True,
                        "selection": {"action": "confirm", "plan_id": plan_id}},
        "reject_cta": {"label": "Cancel",
                       "selection": {"action": "cancel", "plan_id": plan_id}},
    }


def quote_payload(title: str, muted_line: str, quotes: list[dict]) -> dict:
    """Terminal custom card. Quotes are shown exactly as stored, never paraphrased."""
    children = [{"type": "text", "value": muted_line, "style": "muted"}]
    for q in quotes:
        line = f"“{q['text']}”"
        if q.get("speaker"):
            line += f" — {q['speaker']}"
        children.append({"type": "text", "value": line, "style": "emphasis"})
    return {"root": {"type": "section", "title": title, "children": children}}
