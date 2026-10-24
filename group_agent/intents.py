"""Rule-based intent router with an asi1-mini fallback. Never touches the DB."""

import re

from shared.llm import LLM

SELECTION_INTENTS = {"vote": "vote_select", "confirm": "confirm", "cancel": "cancel",
                     "lock": "lock"}

RULES: list[tuple[str, re.Pattern]] = [
    ("join", re.compile(r"^\s*join\s+(\S+)\s+as\s+(.+?)\s*$", re.I)),
    ("recap", re.compile(r"^\s*recap\b\s*[:\-]?", re.I)),
    ("help", re.compile(r"^\s*(help|\?|what can you do|how does (this|it) work|commands)\b", re.I)),
    ("lock", re.compile(r"\block\b(\s+it)?(\s+in)?", re.I)),
    ("plan", re.compile(r"^\s*(plan|organi[sz]e|set up)\b|\bplan (something|a|an|the)\b|"
                        r"\b(hang ?out|get together)\b.*\b(this|next|tonight|tomorrow)", re.I)),
    ("lore_question", re.compile(
        r"\b(what did we|what have we|remember|last (time|summer|winter|spring|fall|autumn|year|month)|"
        r"\bago\b|have we (ever|done)|how many times|when did we|who (came|was there)|lore)\b", re.I)),
    ("vote_open", re.compile(r"\b(what'?s the plan|show (me )?(the )?options|options|vote|voting|"
                             r"ballot)\b", re.I)),
    ("plan", re.compile(r"\b(let'?s (do|go|get)|something (for|with)|this weekend|tonight|"
                        r"tomorrow|hang ?out)\b", re.I)),
]


def classify_rules(text: str, selection: dict) -> str | None:
    # join/recap are anchored commands; they win over words a prose selection might pick up
    for name, pattern in RULES[:2]:
        if pattern.search(text):
            return name
    action = selection.get("action")
    if action in SELECTION_INTENTS:
        if action == "vote" and not selection.get("option_id"):
            return "vote_open"
        return SELECTION_INTENTS[action]
    for name, pattern in RULES[2:]:
        if pattern.search(text):
            return name
    return None


async def classify(text: str, selection: dict, llm: LLM) -> str:
    """Rules first; one asi1-mini call only if no rule matched."""
    intent = classify_rules(text, selection)
    if intent:
        return intent
    if not text.strip():
        return "help"
    return await llm.intent(text)
