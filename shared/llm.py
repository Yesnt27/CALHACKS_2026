"""ASI:One client (OpenAI-compatible), prompts, defensive JSON parsing, template fallbacks.

Every call: "Return only JSON." -> parse -> on failure retry once with the parse
error -> on failure (or no API key / network error) use a deterministic template.
Prompt listeners see every prompt that would be sent, even in template mode, so
tests can scan prompts for private data.
"""

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Callable

from shared.config import TZ, env, iso, parse_iso

log = logging.getLogger("lore.llm")

ASI_BASE_URL = "https://api.asi1.ai/v1"

CATEGORIES = [
    "karaoke", "board_games", "food", "night_market", "outdoors", "beach", "hike",
    "live_music", "comedy", "arcade", "bowling", "climbing", "museum", "movies",
    "bar", "escape_room", "mini_golf", "art_class", "dessert", "sports", "picnic",
]

GROUP_INTENTS = ["join", "plan", "vote_open", "vote_select", "lock", "recap",
                 "lore_question", "help"]

# ------------------------------------------------------------------ JSON utils


def parse_json(text: str) -> Any:
    """Tolerates ```json fences and prose around a single JSON object."""
    if text is None:
        raise ValueError("empty response")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S).strip()
    try:
        return json.loads(t)
    except ValueError:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found")
        return json.loads(t[start:end + 1])


# ------------------------------------------------------------------ pitch input

PUBLIC_OPTION_FIELDS = frozenset({
    "option_id", "title", "category", "start", "end", "place", "indoor",
    "cost_band", "cost_per_person", "source",
})
WEATHER_FIELDS = frozenset({"rain_likely", "max_precip", "temp_c"})


def build_pitch_input(option_public: dict, weather: dict, novelty_note: str) -> dict:
    """The ONLY input the PITCH prompt may see (privacy invariant 5)."""
    extra = set(option_public) - PUBLIC_OPTION_FIELDS
    if extra:
        raise ValueError(f"non-public option fields passed to pitch: {sorted(extra)}")
    extra_w = set(weather) - WEATHER_FIELDS
    if extra_w:
        raise ValueError(f"unexpected weather fields: {sorted(extra_w)}")
    return {"option": dict(option_public), "weather": dict(weather),
            "novelty_note": str(novelty_note)}


# ------------------------------------------------------------------ templates

IDEA_BANK = [
    {"title": "Board game café night", "category": "board_games", "indoor": True, "cost_band": "$",
     "alcohol_centric": False, "duration_h": 2.5, "search_query": "board game cafe", "source": "place"},
    {"title": "Karaoke room", "category": "karaoke", "indoor": True, "cost_band": "$$",
     "alcohol_centric": False, "duration_h": 2.0, "search_query": "karaoke", "source": "place"},
    {"title": "Night market food crawl", "category": "night_market", "indoor": False, "cost_band": "$",
     "alcohol_centric": False, "duration_h": 2.5, "search_query": "night market", "source": "place"},
    {"title": "Bouldering session", "category": "climbing", "indoor": True, "cost_band": "$$",
     "alcohol_centric": False, "duration_h": 2.0, "search_query": "climbing gym", "source": "place"},
    {"title": "Retro arcade", "category": "arcade", "indoor": True, "cost_band": "$",
     "alcohol_centric": False, "duration_h": 2.0, "search_query": "arcade", "source": "place"},
    {"title": "Bowling + fries", "category": "bowling", "indoor": True, "cost_band": "$$",
     "alcohol_centric": False, "duration_h": 2.0, "search_query": "bowling", "source": "place"},
    {"title": "Escape room", "category": "escape_room", "indoor": True, "cost_band": "$$",
     "alcohol_centric": False, "duration_h": 1.5, "search_query": "escape room", "source": "place"},
    {"title": "Sunset picnic at Dolores", "category": "picnic", "indoor": False, "cost_band": "free",
     "alcohol_centric": False, "duration_h": 2.0, "search_query": "dolores park", "source": "place"},
    {"title": "Lands End sunset walk", "category": "hike", "indoor": False, "cost_band": "free",
     "alcohol_centric": False, "duration_h": 2.0, "search_query": "lands end trail", "source": "place"},
    {"title": "Late night at the museum", "category": "museum", "indoor": True, "cost_band": "$$",
     "alcohol_centric": False, "duration_h": 2.5, "search_query": "museum", "source": "place"},
    {"title": "Comedy show", "category": "comedy", "indoor": True, "cost_band": "$$",
     "alcohol_centric": False, "duration_h": 2.0, "search_query": "comedy", "source": "event"},
    {"title": "Live music night", "category": "live_music", "indoor": True, "cost_band": "$$",
     "alcohol_centric": False, "duration_h": 2.5, "search_query": "concert", "source": "event"},
    {"title": "Mini golf", "category": "mini_golf", "indoor": True, "cost_band": "$$",
     "alcohol_centric": False, "duration_h": 1.5, "search_query": "mini golf", "source": "place"},
    {"title": "Pottery drop-in class", "category": "art_class", "indoor": True, "cost_band": "$$$",
     "alcohol_centric": False, "duration_h": 2.5, "search_query": "pottery class", "source": "place"},
    {"title": "Dessert crawl", "category": "dessert", "indoor": False, "cost_band": "$",
     "alcohol_centric": False, "duration_h": 2.0, "search_query": "dessert", "source": "place"},
    {"title": "Dim sum feast", "category": "food", "indoor": True, "cost_band": "$$",
     "alcohol_centric": False, "duration_h": 2.0, "search_query": "dim sum", "source": "place"},
    {"title": "Brewery hangout", "category": "bar", "indoor": True, "cost_band": "$$",
     "alcohol_centric": True, "duration_h": 2.5, "search_query": "brewery", "source": "place"},
    {"title": "Ocean Beach bonfire", "category": "beach", "indoor": False, "cost_band": "free",
     "alcohol_centric": False, "duration_h": 2.5, "search_query": "ocean beach fire pit", "source": "place"},
    {"title": "Warriors watch party", "category": "sports", "indoor": True, "cost_band": "$",
     "alcohol_centric": False, "duration_h": 2.5, "search_query": "basketball", "source": "event"},
    {"title": "Movie night at the Roxie", "category": "movies", "indoor": True, "cost_band": "$",
     "alcohol_centric": False, "duration_h": 2.5, "search_query": "movie theater", "source": "place"},
]


def template_ideas(recent_counts: dict[str, int], rain_any: bool, n: int = 8) -> list[dict]:
    """Deterministic idea list: novel categories first, indoor first when rain is likely."""
    def key(idea: dict):
        seen = recent_counts.get(idea["category"], 0)
        return (seen > 0, rain_any and not idea["indoor"], seen, IDEA_BANK.index(idea))
    ranked = sorted(IDEA_BANK, key=key)
    return [dict(i) for i in ranked[:n]]


def template_pitch(inp: dict) -> str:
    o, w = inp["option"], inp["weather"]
    if w.get("rain_likely"):
        line = "Dry and cozy if it rains" if o.get("indoor") else "Bring a jacket, rain is likely"
    elif not o.get("indoor"):
        line = "Clear skies, perfect for being outside"
    else:
        line = "Easy, low-key and indoors"
    if o.get("cost_band") == "free":
        line += ", and it's free"
    if inp.get("novelty_note"):
        line += f" ({inp['novelty_note']})"
    return line[:89]


_WORD_NUM = {"half an hour": 30, "an hour": 60, "one hour": 60, "45 minutes": 45}


def _hhmm(hour: int, minute: int, ampm: str | None) -> str:
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
    elif 5 <= hour <= 11:
        hour += 12          # "home by 11" means 11 pm
    elif hour == 12:
        hour = 0            # "by 12" means midnight
    return f"{hour % 24:02d}:{minute:02d}"


def template_constraints(text: str) -> dict:
    """Rule-based constraint parser. Returns only the keys the text mentions.

    A value of None means "remove that limit".
    """
    out: dict = {}
    low = text.lower()
    used: list[tuple[int, int]] = []

    if m := hit_or_none(r"\bno (budget|price) (limit|cap)\b|\bany budget\b", low):
        used.append(m.span()); out["budget_cap_pp"] = None
    elif m := hit_or_none(
            r"(?:budget|under|max(?:imum)?|less than|up to|cap|spend)\D{0,12}?\$?\s?(\d+(?:\.\d+)?)"
            r"|\$\s?(\d+(?:\.\d+)?)", low):
        used.append(m.span()); out["budget_cap_pp"] = float(m.group(1) or m.group(2))
    if m := hit_or_none(r"alcohol is fine|i drink|drinks are fine|ok with (alcohol|drinking|bars)", low):
        used.append(m.span()); out["no_alcohol"] = False
    elif m := hit_or_none(r"\bno (alcohol|drinking|drinks|booze|bars)\b|don'?t drink|do not drink|"
                          r"\bsober\b|not drinking|\bi don'?t do bars\b", low):
        used.append(m.span()); out["no_alcohol"] = True
    if m := hit_or_none(r"\bno curfew\b", low):
        used.append(m.span()); out["latest_end"] = None
    elif m := hit_or_none(r"(?:home|back|out|done|leave|in bed)\s+by\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
                          r"|curfew\D{0,8}?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
                          r"|(?:latest|until|till|no later than)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", low):
        used.append(m.span())
        g = m.groups()
        for i in (0, 3, 6):
            if g[i]:
                out["latest_end"] = _hhmm(int(g[i]), int(g[i + 1] or 0), g[i + 2])
                break
    travel = None
    if m := hit_or_none(r"(\d+)\s*(?:min|mins|minutes)\b", low):
        used.append(m.span()); travel = int(m.group(1))
    elif m := hit_or_none(r"(\d+(?:\.\d+)?)\s*(?:hr|hrs|hour|hours)\b", low):
        used.append(m.span()); travel = round(float(m.group(1)) * 60)
    else:
        for phrase, mins in _WORD_NUM.items():
            if (i := low.find(phrase)) >= 0:
                used.append((i, i + len(phrase))); travel = mins
                break
    if travel is not None:
        out["max_travel_min"] = travel
    # "other": clauses that no rule touched, kept verbatim and local.
    leftovers = []
    pos = 0
    for clause in re.split(r"(?<=[.;,\n!])", text):
        start, end = pos, pos + len(clause)
        pos = end
        if clause.strip() and not any(s < end and e > start for s, e in used):
            leftovers.append(clause.strip(" ,;\n"))
    if leftovers:
        out["other"] = " ".join(leftovers).strip()
    return out


def hit_or_none(pattern: str, text: str):
    return re.search(pattern, text)


LOGISTICS_RE = re.compile(r"\?\s*$|\b(tonight|tomorrow|omw|on my way|running late|bring|meet (at|you)|"
                          r"who'?s (in|free|coming)|venmo)\b", re.I)


def template_discord_lore(messages: list[dict], known: list[dict]) -> list[dict]:
    """Offline fallback: chat lines from a known hangout's day (until noon the next day).

    Never creates hangouts; that needs the LLM to tell a plan from something that happened.
    """
    out = []
    for h in known:
        start = datetime.combine(date.fromisoformat(h["date"][:10]), time(0), TZ)
        end = start + timedelta(days=1, hours=12)
        ids = [m["id"] for m in messages
               if start <= parse_iso(m["at"]) < end and len(m["text"]) >= 12
               and not LOGISTICS_RE.search(m["text"])]
        if ids:
            out.append({"hangout_id": h["id"], "quote_ids": ids})
    return out


def template_lore_answer(question: str, facts: list[dict]) -> str:
    if not facts:
        return "I couldn't find anything in the lore for that. Try a season, a month, or an activity."
    lines = []
    for f in facts[:5]:
        who = f"{len(f['attendees'])} of you" if f.get("attendees") else ""
        place = f" at {f['place']}" if f.get("place") else ""
        lines.append(f"• {f['date']}: {f['title']}{place}" + (f" ({who})" if who else ""))
    return "Here's what the lore says:\n" + "\n".join(lines)


# ------------------------------------------------------------------ client


class LLM:
    def __init__(self, api_key: str | None = None, base_url: str = ASI_BASE_URL,
                 model_large: str | None = None, model_small: str | None = None,
                 timeout: float = 25.0):
        api_key = api_key if api_key is not None else env("ASI_ONE_API_KEY")
        self.model_large = model_large or env("ASI_MODEL_LARGE", "asi1") or "asi1"
        self.model_small = model_small or env("ASI_MODEL_SMALL", "asi1-mini") or "asi1-mini"
        self.client = None
        if api_key:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout,
                                      max_retries=0)
        self.listeners: list[Callable[[str, list[dict]], None]] = []
        self.tokens_used = 0

    @property
    def live(self) -> bool:
        return self.client is not None

    def on_prompt(self, fn: Callable[[str, list[dict]], None]) -> None:
        self.listeners.append(fn)

    async def json_call(self, purpose: str, model: str, system: str, user: str,
                        validate: Callable[[Any], Any], fallback: Callable[[], Any],
                        history: list[dict] | None = None) -> Any:
        messages = [{"role": "system", "content": system + "\nReturn only JSON."}]
        messages += history or []
        messages.append({"role": "user", "content": user})
        for fn in self.listeners:
            fn(purpose, messages)
        if self.client is None:
            return fallback()
        for attempt in range(2):
            text = ""
            try:
                resp = await self.client.chat.completions.create(
                    model=model, messages=messages, temperature=0.4)
                if resp.usage:
                    self.tokens_used += resp.usage.total_tokens or 0
                    log.info("llm %s (%s) tokens: prompt=%s completion=%s total_session=%s",
                             purpose, model, resp.usage.prompt_tokens,
                             resp.usage.completion_tokens, self.tokens_used)
                text = resp.choices[0].message.content or ""
                return validate(parse_json(text))
            except (ValueError, KeyError, TypeError, AssertionError) as exc:
                log.warning("llm %s: bad JSON on attempt %d: %s", purpose, attempt + 1, exc)
                messages = messages + [
                    {"role": "assistant", "content": text[:2000]},
                    {"role": "user", "content": f"That failed to parse: {exc}. "
                                                f"Return only the JSON object."}]
            except Exception as exc:  # network, auth, timeout
                log.warning("llm %s: call failed (%s); using template", purpose, exc)
                break
        return fallback()

    # ---------------------------------------------------------- prompts

    async def ideas(self, home_label: str, windows: list[dict], weather: list[dict],
                    recent_counts: dict[str, int], group_size: int) -> list[dict]:
        rain_any = any(w.get("rain_likely") for w in weather)
        system = (
            "You suggest hangout ideas for a friend group. Output "
            '{"ideas": [{"title", "category", "indoor": bool, "cost_band": "free|$|$$|$$$", '
            '"alcohol_centric": bool, "duration_h": float, "search_query", "source": "event|place"}]}. '
            f"Exactly 8 ideas. category must be one of: {', '.join(CATEGORIES)}. "
            "At least 4 ideas must use categories that are absent from recent_counts. "
            "search_query is a short phrase to find a real venue or event near the home base. "
            "Prefer indoor ideas for windows where rain is likely.")
        user = json.dumps({"home_base": home_label, "windows": windows, "weather": weather,
                           "recent_counts": recent_counts, "group_size": group_size})

        def validate(data):
            ideas = data["ideas"]
            assert isinstance(ideas, list) and len(ideas) >= 4, "need a list of ideas"
            out = []
            for i in ideas[:8]:
                cat = str(i["category"]).strip().lower().replace(" ", "_")
                out.append({
                    "title": str(i["title"])[:80],
                    "category": cat if cat in CATEGORIES else "food",
                    "indoor": bool(i.get("indoor", True)),
                    "cost_band": i.get("cost_band") if i.get("cost_band") in ("free", "$", "$$", "$$$") else "$$",
                    "alcohol_centric": bool(i.get("alcohol_centric", False)),
                    "duration_h": max(1.0, min(4.0, float(i.get("duration_h", 2.5)))),
                    "search_query": str(i.get("search_query") or i["title"])[:60],
                    "source": "event" if i.get("source") == "event" else "place",
                })
            novel = sum(1 for i in out if recent_counts.get(i["category"], 0) == 0)
            assert novel >= 4, f"only {novel} ideas in new categories, need 4"
            return out

        return await self.json_call("IDEAS", self.model_large, system, user, validate,
                                    lambda: template_ideas(recent_counts, rain_any))

    async def pitches(self, inputs: list[dict]) -> dict[str, str]:
        """inputs: outputs of build_pitch_input only."""
        for inp in inputs:
            assert set(inp) == {"option", "weather", "novelty_note"}
        system = ('Write one upbeat line per option for a group chat. Output '
                  '{"pitches": [{"option_id", "line"}]}. Each line under 90 characters. '
                  "Use only the facts given. Do not mention any individual person.")
        user = json.dumps(inputs)
        ids = [i["option"]["option_id"] for i in inputs]

        def validate(data):
            out = {}
            for p in data["pitches"]:
                if p["option_id"] in ids:
                    out[p["option_id"]] = str(p["line"]).strip()[:89]
            assert set(out) == set(ids), "missing pitches"
            return out

        return await self.json_call("PITCH", self.model_large, system, user, validate,
                                    lambda: {i["option"]["option_id"]: template_pitch(i) for i in inputs})

    async def constraints(self, text: str) -> dict:
        system = ("Extract hangout constraints from the user's message. Output a JSON object "
                  'with only the keys the message mentions, from: "budget_cap_pp" (number, USD per person), '
                  '"no_alcohol" (bool), "latest_end" ("HH:MM" 24h), "max_travel_min" (int), '
                  '"other" (string: anything else, verbatim). Use null to remove a limit.')

        def validate(data):
            assert isinstance(data, dict)
            out = {}
            if "budget_cap_pp" in data:
                v = data["budget_cap_pp"]
                out["budget_cap_pp"] = None if v is None else float(v)
            if "no_alcohol" in data and data["no_alcohol"] is not None:
                out["no_alcohol"] = bool(data["no_alcohol"])
            if "latest_end" in data:
                v = data["latest_end"]
                if v is not None:
                    assert re.fullmatch(r"\d{2}:\d{2}", str(v)), "latest_end must be HH:MM"
                out["latest_end"] = v
            if "max_travel_min" in data:
                v = data["max_travel_min"]
                out["max_travel_min"] = None if v is None else int(v)
            if data.get("other"):
                out["other"] = str(data["other"])
            return out

        return await self.json_call("CONSTRAINTS", self.model_small, system, text, validate,
                                    lambda: template_constraints(text))

    async def intent(self, text: str) -> str:
        system = ("Classify the message sent to a friend-group hangout planner into exactly one "
                  f'intent from {GROUP_INTENTS}. Output {{"intent": "<intent>"}}.')

        def validate(data):
            assert data["intent"] in GROUP_INTENTS, "unknown intent"
            return data["intent"]

        return await self.json_call("INTENT", self.model_small, system, text, validate,
                                    lambda: "help")

    async def window(self, text: str, now: datetime) -> dict | None:
        system = ("Turn the time phrase into a planning window in America/Los_Angeles. Output "
                  '{"start": ISO8601 with offset, "end": ISO8601 with offset}. '
                  "Evenings run 17:00-23:30; weekend days 10:00-23:30.")
        user = json.dumps({"now": iso(now), "weekday": now.strftime("%A"), "text": text})

        def validate(data):
            s, e = parse_iso(data["start"]), parse_iso(data["end"])
            assert e > s, "end must be after start"
            assert s < now + timedelta(days=45), "window too far out"
            return {"start": iso(s), "end": iso(max(e, now))}

        return await self.json_call("WINDOW", self.model_small, system, user, validate,
                                    lambda: None)

    async def recap(self, text_with_placeholders: str, placeholders: list[str]) -> str:
        system = ("Tidy this hangout recap into at most two short lines. Keep every placeholder "
                  "like [[Q1]] exactly as written; do not invent details. "
                  'Output {"recap": "<two lines>"}.')

        def validate(data):
            out = str(data["recap"]).strip()
            missing = [p for p in placeholders if p not in out]
            assert not missing, f"dropped placeholders {missing}"
            return out

        return await self.json_call("RECAP", self.model_large, system, text_with_placeholders,
                                    validate, lambda: " ".join(text_with_placeholders.split()))

    async def lore_answer(self, question: str, facts: list[dict], history: list[dict]) -> str:
        system = ("You are the friend group's lore keeper. Answer from the facts only, in at most "
                  "3 sentences, warm and specific. Never quote or paraphrase quotes; they are shown "
                  'separately. Output {"answer": "..."}.')
        user = json.dumps({"question": question, "facts": facts})

        def validate(data):
            return str(data["answer"]).strip()

        return await self.json_call("LORE", self.model_large, system, user, validate,
                                    lambda: template_lore_answer(question, facts),
                                    history=history[-10:])

    async def discord_lore(self, messages: list[dict], members: list[str],
                           known: list[dict]) -> list[dict]:
        """messages: [{id, at, speaker, text}] from members only, private lines already removed.

        Returns [{hangout_id, quote_ids}] for known hangouts and
        [{hangout_id: None, date, title, category, place, attendees, quote_ids}] for new ones.
        The model only picks message ids; quote text is copied from Discord by the caller.
        """
        system = (
            "You read a friend group's chat log and connect it to the group's hangout history. Output "
            '{"hangouts": [{"hangout_id": int or null, "date": "YYYY-MM-DD", "title", "category", '
            '"place", "attendees": [member names], "quote_ids": [message ids]}]}. '
            "Use hangout_id when lines are about a known hangout. Use null only for a hangout the chat "
            "shows clearly happened (not one that was only planned), and fill date, title, category, "
            f"place and attendees. category must be one of: {', '.join(CATEGORIES)}. quote_ids: up to 3 "
            "funny or memorable lines per hangout, as ids from the log; never logistics, plans or "
            "questions. Never invent hangouts, people or places.")
        user = json.dumps({"members": members, "known_hangouts": known, "messages": messages},
                          ensure_ascii=False)
        by_id = {m["id"] for m in messages}
        known_ids = {h["id"] for h in known}
        days = sorted(m["at"][:10] for m in messages)

        def validate(data):
            out = []
            for h in data["hangouts"]:
                ids = [str(i) for i in (h.get("quote_ids") or []) if str(i) in by_id][:5]
                if h.get("hangout_id") is not None:
                    hid = int(h["hangout_id"])
                    assert hid in known_ids, f"unknown hangout_id {hid}"
                    out.append({"hangout_id": hid, "quote_ids": ids})
                    continue
                day = date.fromisoformat(str(h["date"])[:10]).isoformat()
                assert days and days[0] <= day <= days[-1], f"date {day} is outside the chat log"
                cat = str(h.get("category", "")).strip().lower().replace(" ", "_")
                out.append({"hangout_id": None, "date": day, "title": str(h["title"]).strip()[:80],
                            "category": cat if cat in CATEGORIES else "food",
                            "place": str(h.get("place") or "").strip()[:80] or None,
                            "attendees": [a for a in (h.get("attendees") or []) if a in members],
                            "quote_ids": ids})
            return out

        return await self.json_call("DISCORD_LORE", self.model_large, system, user, validate,
                                    lambda: template_discord_lore(messages, known))
