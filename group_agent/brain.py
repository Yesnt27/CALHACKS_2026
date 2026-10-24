"""Group agent logic, independent of the uAgents runtime.

agent.py wires this to ctx.send; scripts/simulate_plan.py and the tests wire it to
in-process Member brains. Chat and protocol handlers only store and reply; all
pipeline work happens in tick(), which the agent calls every second.
"""

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from uagents import Model
from uagents_core.contrib.protocols.chat import ChatMessage

from group_agent import planner
from group_agent.intents import classify
from group_agent.lore_db import LoreDB
from group_agent.scoring import score_candidates, top_distinct
from group_agent.sources import events as events_src
from group_agent.sources import weather as weather_src
from group_agent.sources.places import Places
from shared.cards import (card_message, carousel_item, carousel_payload, extract_selection,
                          quote_payload, review_payload, text_message)
from shared.config import env, env_bool, env_float, iso, now_local, parse_iso
from shared.llm import LLM, build_pitch_input
from shared.protocol import (FITS, AvailabilityReply, AvailabilityRequest, EvaluateReply,
                             EvaluateRequest, OptionSpec)

log = logging.getLogger("lore.group")

Send = Callable[[str, Model, str | None], Awaitable[None]]

HELP = ("I'm Lore Keeper, your group's hangout planner. Join with `join <invite code> as <name>`. "
        "Say \"plan something this weekend\" (or tonight / tomorrow / friday) and I'll privately check "
        "everyone's agent for free time and limits, then show 3 options to vote on. \"what's the plan\" "
        "reopens the vote, \"lock it in\" confirms the leader, \"recap: …\" saves how it went (quotes are "
        "kept word for word), and you can ask things like \"what did we do last summer?\"")

QUOTE_RE = re.compile(r'"([^"\n]+)"|“([^”\n]+)”')
SPEAKER_AFTER = re.compile(r"^\s*(?:[—–-]+\s*|,?\s*(?:said|says)\s+)([A-Z][\w'\-]*)")
SPEAKER_BEFORE = re.compile(r"([A-Z][\w'\-]*)\s*(?:said|says|:)\s*$")
ATTENDEES_RE = re.compile(r"\b(?:with|attendees?:|who came:?|came:)\s+((?:[A-Z][a-z]+)(?:\s*(?:,|and|&)\s*[A-Z][a-z]+)*)")
SEASONS = {"summer": (6, 8), "fall": (9, 11), "autumn": (9, 11), "spring": (3, 5), "winter": (12, 2)}


@dataclass
class GroupConfig:
    invite_code: str = ""
    home_lat: float = 37.7599
    home_lng: float = -122.4148
    home_label: str = "Mission, SF"
    push_enabled: bool = False
    deadline_s: float = 10.0

    @classmethod
    def from_env(cls) -> "GroupConfig":
        return cls(invite_code=env("GROUP_INVITE_CODE"),
                   home_lat=env_float("HOME_BASE_LAT", 37.7599),
                   home_lng=env_float("HOME_BASE_LNG", -122.4148),
                   home_label=env("HOME_BASE_LABEL", "Mission, SF"),
                   push_enabled=env_bool("PUSH_ENABLED", False))


def extract_quotes(text: str) -> tuple[str, list[dict], list[str]]:
    """Replace quoted spans with [[Qn]]; return (text_with_placeholders, quotes, original_spans).

    Quote text is the exact substring between the quote marks (stored byte-for-byte).
    """
    quotes, spans, out, pos = [], [], [], 0
    for n, m in enumerate(QUOTE_RE.finditer(text), 1):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        before, after = text[pos:m.start()], text[m.end():]
        speaker = None
        if sm := SPEAKER_AFTER.match(after):
            speaker = sm.group(1)
        elif sm := SPEAKER_BEFORE.search(before):
            speaker = sm.group(1)
        quotes.append({"text": inner, "speaker": speaker})
        spans.append(m.group(0))
        out.append(before)
        out.append(f"[[Q{n}]]")
        pos = m.end()
    out.append(text[pos:])
    return "".join(out), quotes, spans


class GroupBrain:
    def __init__(self, db: LoreDB, llm: LLM, config: GroupConfig, places: Places | None = None,
                 clock: Callable[[], datetime] = now_local,
                 weather_fn=weather_src.hourly, events_fn=events_src.search):
        self.db, self.llm, self.cfg = db, llm, config
        self.places = places or Places()
        self.clock = clock
        self.weather_fn, self.events_fn = weather_fn, events_fn
        self._busy = False

    # ================================================================ chat

    async def on_chat(self, sender: str, msg: ChatMessage, send: Send,
                      session: str | None = None, history: list[dict] | None = None) -> None:
        text = msg.text().strip()
        selection = extract_selection(msg)
        if (m := re.match(r"^\s*vote\s+(?:for\s+)?(?:option\s+)?#?([1-9])\b", text, re.I)) \
                and not selection.get("option_id"):
            selection["option_index"] = int(m.group(1))
        if selection.get("option_index") and not selection.get("option_id"):
            selection.setdefault("action", "vote")
        if not text and not selection:
            return
        intent = await classify(text, selection, self.llm)
        if selection.get("option_index") and intent == "vote_open":
            intent = "vote_select"
        log.info("chat from %s -> intent %s", sender, intent)

        async def reply(m: ChatMessage) -> None:
            await send(sender, m, session)

        if intent == "help":
            return await reply(text_message(HELP))
        if intent == "join":
            return await self._join(sender, text, reply)
        member = self.db.member_for_sender(sender)
        if member is None:
            return await reply(text_message(
                "You're not in this group yet. Say `join <invite code> as <your name>`."))
        handler = {
            "plan": self._plan, "vote_open": self._vote_open, "vote_select": self._vote_select,
            "lock": self._lock, "confirm": self._confirm, "cancel": self._cancel,
            "recap": self._recap, "lore_question": self._lore_question,
        }.get(intent)
        if handler is None:
            return await reply(text_message(HELP))
        await handler(sender, member, text, selection, reply, send, session, history or [])
        if intent not in ("plan", "lock", "confirm", "cancel"):
            await self._maybe_majority_review(sender, reply)

    async def _join(self, sender, text, reply):
        m = re.match(r"^\s*join\s+(\S+)\s+as\s+(.+?)\s*$", text, re.I)
        if not m:
            return await reply(text_message("Use: `join <invite code> as <your name>`."))
        code, name = m.group(1), m.group(2)
        if not self.cfg.invite_code or not secrets.compare_digest(code, self.cfg.invite_code):
            return await reply(text_message("That invite code doesn't match."))
        found = self.db.find_member(name)
        if not found:
            return await reply(text_message(
                f"I don't have a member called {name}. Members: {', '.join(self.db.member_names())}."))
        self.db.bind_sender(found, sender)
        log.info("bound ASI:One sender %s to member %s", sender, found)
        await reply(text_message(f"Welcome, {found}! You're in. Say \"plan something this weekend\" "
                                 "or ask \"what did we do last summer?\""))

    # ---------------------------------------------------------------- plan

    async def _plan(self, sender, member, text, sel, reply, send, session, history):
        now = self.clock()
        cur = self.db.current_plan()
        if cur and cur["state"] in ("collecting_availability", "evaluating"):
            return await reply(text_message(f"Already on it ({cur['id']}) — options in a few seconds."))
        note = ""
        if cur:  # a new plan supersedes an open vote
            self.db.finish_plan(cur["id"], "cancelled")
            note = " (I closed the previous vote.)"
        win = planner.parse_window_rules(text, now)
        if win is None:
            parsed = await self.llm.window(text, now)
            win = (parse_iso(parsed["start"]), parse_iso(parsed["end"])) if parsed else None
        if win is None:
            win = planner.default_window(now)
        plan_id = "p_" + secrets.token_hex(3)
        self.db.create_plan(plan_id, sender, iso(win[0]), iso(win[1]),
                            iso(now + timedelta(seconds=self.cfg.deadline_s)), iso(now),
                            str(session) if session else None)
        self.db.update_plan(plan_id, work={"window_label": _window_label(text, win)})
        await reply(text_message(
            f"Checking calendars and asking everyone's agent privately…{note}"))
        for name, address in self.db.member_addresses().items():
            await send(address, AvailabilityRequest(plan_id=plan_id, window_start=iso(win[0]),
                                                    window_end=iso(win[1])), None)

    async def _vote_open(self, sender, member, text, sel, reply, send, session, history, hint=""):
        plan = self.db.plan(sel["plan_id"]) if sel.get("plan_id") else self.db.current_plan()
        if not plan or plan["state"] in ("locked", "cancelled"):
            return await reply(text_message(
                hint or "No vote open right now. Say \"plan something this weekend\" to start one."))
        if plan["state"] != "voting":
            return await reply(text_message("Still gathering answers — try again in a few seconds."))
        await reply(self._carousel_message(plan, hint))

    async def _vote_select(self, sender, member, text, sel, reply, send, session, history):
        plan = self.db.plan(sel["plan_id"]) if sel.get("plan_id") else self.db.current_plan()
        if not plan or plan["state"] != "voting":
            return await self._vote_open(sender, member, text, {}, reply, send, session, history,
                                         hint="That vote is closed.")
        ids = [o["option_id"] for o in plan["options"]]
        option_id = sel.get("option_id")
        if not option_id and sel.get("option_index"):
            i = int(sel["option_index"]) - 1
            option_id = ids[i] if 0 <= i < len(ids) else None
        if option_id not in ids:
            return await self._vote_open(sender, member, text, {}, reply, send, session, history,
                                         hint="I couldn't read that vote — tap Vote on one of these.")
        self.db.upsert_vote(plan["id"], member, option_id)
        votes = self.db.votes(plan["id"])
        n = len(self.db.member_names())
        msg = f"Vote counted ({len(votes)}/{n} voted)."
        leader, count = _leader(plan, votes)
        if count * 2 > n:
            msg += f" {leader['title']} has a majority — say \"lock it in\" to confirm."
        await reply(text_message(msg))

    async def _lock(self, sender, member, text, sel, reply, send, session, history):
        plan = self.db.current_plan()
        if not plan or plan["state"] != "voting":
            return await reply(text_message("Nothing to lock yet."))
        await self._send_review(plan, reply)

    async def _send_review(self, plan, reply):
        votes = self.db.votes(plan["id"])
        if not votes:
            return await reply(text_message("No votes yet — say \"vote\" to see the options."))
        leader, count = _leader(plan, votes)
        work = plan["work"]
        work["review_for"] = leader["option_id"]
        work["review_sent"] = True
        self.db.update_plan(plan["id"], work=work)
        narration = (f"Lock in {leader['title']} — {leader['when']} at {leader['place']}? "
                     f"({count} vote{'s' if count != 1 else ''}) Reply confirm or cancel.")
        await reply(card_message(narration, "review",
                                 review_payload(plan["id"], leader, leader["coming"])))

    async def _maybe_majority_review(self, sender, reply):
        """Pull-based: once a majority exists, the planner gets the review card on their next message."""
        plan = self.db.current_plan()
        if not plan or plan["state"] != "voting" or plan["planner_sender"] != sender:
            return
        if plan["work"].get("review_sent"):
            return
        votes = self.db.votes(plan["id"])
        _, count = _leader(plan, votes) if votes else (None, 0)
        if count * 2 > len(self.db.member_names()):
            await self._send_review(plan, reply)

    async def _confirm(self, sender, member, text, sel, reply, send, session, history):
        plan = self.db.plan(sel["plan_id"]) if sel.get("plan_id") else self.db.current_plan()
        if not plan or plan["state"] != "voting":
            return await reply(text_message("There's no plan waiting for confirmation."))
        votes = self.db.votes(plan["id"])
        chosen_id = plan["work"].get("review_for") or (_leader(plan, votes)[0] or {}).get("option_id")
        option = next((o for o in plan["options"] if o["option_id"] == chosen_id), None)
        if option is None:
            return await reply(text_message("No votes yet — say \"vote\" to see the options."))
        self.db.add_hangout(option["start"], option["title"], [option["category"]], option["place"],
                            option["coming"], status="planned")
        self.db.finish_plan(plan["id"], "locked", chosen_option=option["option_id"])
        msg = (f"Locked in: {option['title']} — {option['when']} at {option['place']}. "
               f"Coming: {', '.join(option['coming']) or 'TBD'}. Send \"recap: …\" afterwards and "
               "I'll add it to the lore.")
        await reply(text_message(msg))
        await self._push(sender, text_message(msg), send)

    async def _cancel(self, sender, member, text, sel, reply, send, session, history):
        plan = self.db.plan(sel["plan_id"]) if sel.get("plan_id") else self.db.current_plan()
        if not plan or plan["state"] in ("locked", "cancelled"):
            return await reply(text_message("Nothing to cancel."))
        self.db.finish_plan(plan["id"], "cancelled")
        await reply(text_message("Cancelled. Say \"plan something\" whenever you want to try again."))

    async def _push(self, sender: str, msg: ChatMessage, send: Send) -> None:
        if not self.cfg.push_enabled:
            return
        for name in self.db.member_names():
            to = self.db.sender_for_member(name)
            if to and to != sender:
                await send(to, msg, None)

    # ---------------------------------------------------------------- lore

    async def _recap(self, sender, member, text, sel, reply, send, session, history):
        body = re.sub(r"^\s*recap\b\s*[:\-]?\s*", "", text, flags=re.I)
        if not body:
            return await reply(text_message("Use: `recap: how it went, \"quotes\" — Name`."))
        hangout = self.db.latest_for_recap(iso(self.clock()))
        if hangout is None:
            return await reply(text_message("There's no hangout to attach this to yet."))
        with_ph, quotes, spans = extract_quotes(body)
        placeholders = [f"[[Q{i}]]" for i in range(1, len(quotes) + 1)]
        tidy = await self.llm.recap(with_ph, placeholders)
        for ph, span in zip(placeholders, spans):
            tidy = tidy.replace(ph, span)
        names = []
        if m := ATTENDEES_RE.search(body):
            for raw in re.split(r"\s*(?:,|and|&)\s*", m.group(1)):
                if found := self.db.find_member(raw):
                    names.append(found)
        self.db.update_recap(hangout["id"], tidy, quotes, names or None)
        await reply(text_message(
            f"Added to the lore: {hangout['title']} ({hangout['date'][:10]}). "
            f"Saved {len(quotes)} quote{'s' if len(quotes) != 1 else ''} word for word."))

    async def _lore_question(self, sender, member, text, sel, reply, send, session, history):
        rows, card_title = self._retrieve(text)
        facts = [{"date": h["date"][:10], "title": h["title"], "place": h["place"],
                  "attendees": h["attendees"], "categories": h["categories"],
                  "recap": QUOTE_RE.sub("[quote]", h["recap"] or "")} for h in rows[:6]]
        if history and history[-1].get("content", "").strip() == text:
            history = history[:-1]          # session history already holds this message
        answer = await self.llm.lore_answer(text, facts, history)
        quoted = next((h for h in rows if h["quotes"]), None)
        if quoted is None:
            return await reply(text_message(answer))
        muted = f"{quoted['title']} · {len(quoted['attendees'])} of you"
        await reply(card_message(answer, "custom",
                                 quote_payload(card_title or "From the lore", muted,
                                               quoted["quotes"][:3]), terminal=True))

    def _retrieve(self, text: str) -> tuple[list[dict], str | None]:
        low = text.lower()
        today = self.clock().date()
        if re.search(r"\b(a|one|1) year ago\b|\blast year this\b|\bthis time last year\b", low):
            a = today - timedelta(days=372)
            b = today - timedelta(days=358)
            return self.db.hangouts("substr(date,1,10) BETWEEN ? AND ?",
                                    (a.isoformat(), b.isoformat())), "One year ago this week"
        for season, (m1, m2) in SEASONS.items():
            if re.search(rf"\b(last|this) {season}\b", low):
                if m1 > m2:  # winter spans the new year; use the most recent one
                    end_year = today.year if today.month >= 3 or today.month == 12 else today.year - 1
                    a, b = f"{end_year - 1}-12-01", f"{end_year}-02-29"
                else:
                    year = today.year if ("this" in low or today.month > m2) else today.year - 1
                    a, b = f"{year}-{m1:02d}-01", f"{year}-{m2:02d}-31"
                rows = self.db.hangouts("substr(date,1,10) BETWEEN ? AND ?", (a, b))
                return rows, f"Last {season}"
        if "last month" in low:
            first = today.replace(day=1)
            prev = (first - timedelta(days=1)).replace(day=1)
            return self.db.hangouts("substr(date,1,10) BETWEEN ? AND ?",
                                    (prev.isoformat(), (first - timedelta(days=1)).isoformat())), \
                "Last month"
        words = [w for w in re.findall(r"[a-z]{4,}", low)
                 if w not in {"what", "when", "have", "ever", "last", "time", "remember", "that",
                              "with", "were", "where", "did", "does", "many", "times", "there"}]
        for w in words:
            rows = self.db.hangouts("lower(title) LIKE ? OR lower(categories) LIKE ? OR "
                                    "lower(place) LIKE ? OR lower(recap) LIKE ?",
                                    (f"%{w}%",) * 4)
            if rows:
                return rows, None
        return self.db.recent_hangouts(5), None

    # ================================================================ protocol

    def on_availability(self, sender: str, msg: AvailabilityReply) -> None:
        member = self.db.member_for_address(sender)
        plan = self.db.plan(msg.plan_id)
        if member is None or plan is None or plan["state"] != "collecting_availability":
            log.info("ignoring availability from %s for %s", sender, msg.plan_id)
            return
        slots = [[parse_iso(a).isoformat(), parse_iso(b).isoformat()] for a, b in msg.free_slots]
        self.db.put_reply(msg.plan_id, member, "availability",
                          {"free_slots": slots, "has_calendar": bool(msg.has_calendar)})
        log.info("availability from %s for %s (%d slots)", member, msg.plan_id, len(slots))

    def on_evaluate(self, sender: str, msg: EvaluateReply) -> None:
        member = self.db.member_for_address(sender)
        plan = self.db.plan(msg.plan_id)
        if member is None or plan is None or plan["state"] != "evaluating":
            log.info("ignoring verdicts from %s for %s", sender, msg.plan_id)
            return
        asked = set(plan["work"].get("asked_eval", {}).get(member, []))
        verdicts = {v.option_id: {"fit": v.fit, "travel_min": int(v.travel_min)}
                    for v in msg.verdicts if v.option_id in asked and v.fit in FITS}
        self.db.put_reply(msg.plan_id, member, "evaluate", verdicts)
        log.info("verdicts from %s for %s (%d options)", member, msg.plan_id, len(verdicts))

    # ================================================================ state machine

    async def tick(self, send: Send) -> None:
        """Advance plans whose replies are complete or whose deadline passed."""
        if self._busy:
            return
        self._busy = True
        try:
            now = self.clock()
            for plan in self.db.plans_in("collecting_availability"):
                got = self.db.replies(plan["id"], "availability")
                if len(got) >= len(self.db.member_names()) or now >= parse_iso(plan["deadline"]):
                    await self._after_availability(plan, got, send)
            for plan in self.db.plans_in("evaluating"):
                got = self.db.replies(plan["id"], "evaluate")
                asked = plan["work"].get("asked_eval", {})
                if set(asked) <= set(got) or now >= parse_iso(plan["deadline"]):
                    await self._after_evaluation(plan, got, send)
        except Exception:
            log.exception("tick failed")
        finally:
            self._busy = False

    async def _to_planner(self, plan: dict, msg: ChatMessage, send: Send) -> None:
        await send(plan["planner_sender"], msg, plan.get("planner_session"))

    async def _give_up(self, plan: dict, why: str, send: Send) -> None:
        self.db.finish_plan(plan["id"], "cancelled")
        await self._to_planner(plan, text_message(why), send)

    async def _after_availability(self, plan: dict, avail: dict[str, dict], send: Send) -> None:
        if not avail:
            return await self._give_up(plan, "I couldn't reach anyone's agent. Try again in a bit?", send)
        ws, we = parse_iso(plan["window_start"]), parse_iso(plan["window_end"])
        priority = self.db.priority_members()
        windows = planner.candidate_windows(ws, we, {m: r["free_slots"] for m, r in avail.items()},
                                            priority)
        windows = [w for w in windows if w["free_members"]]
        if not windows:
            return await self._give_up(plan, "Nobody has a free 2.5-hour window then. Try another time?",
                                       send)
        hours = await self.weather_fn(self.cfg.home_lat, self.cfg.home_lng, ws, we)
        weather = [weather_src.summarize(hours, parse_iso(w["start"]), parse_iso(w["end"]))
                   for w in windows]
        counts = self.db.category_counts(10)
        n_members = len(self.db.member_names())
        ideas = await self.llm.ideas(
            self.cfg.home_label,
            [{"start": w["start"], "end": w["end"], "free_count": len(w["free_members"])}
             for w in windows],
            [{"rain_likely": x["rain_likely"], "max_precip": x["max_precip"], "temp_c": x["temp_c"]}
             for x in weather],
            counts, n_members)
        evs = await self.events_fn(self.cfg.home_lat, self.cfg.home_lng, ws, we)
        grounded = await planner.ground_ideas(ideas, windows, evs, self.places,
                                              self.cfg.home_lat, self.cfg.home_lng)
        cands = planner.build_candidates(grounded, windows, weather, counts)
        if not cands:
            return await self._give_up(plan, "I couldn't find real places for any idea. Try again?", send)
        asked: dict[str, list[str]] = {}
        addresses = self.db.member_addresses()
        for member in sorted(avail):
            mine = [c for c in cands if member in c["free_members"]]
            if not mine:
                continue
            asked[member] = [c["option_id"] for c in mine]
            await send(addresses[member], EvaluateRequest(
                plan_id=plan["id"],
                options=[OptionSpec(**planner.option_spec_dict(c)) for c in mine]), None)
        work = plan["work"] | {"windows": windows, "weather": weather, "candidates": cands,
                               "asked_eval": asked, "priority": priority, "counts": counts,
                               "responded": sorted(avail)}
        self.db.update_plan(plan["id"], state="evaluating", work=work,
                            deadline=iso(self.clock() + timedelta(seconds=self.cfg.deadline_s)))
        log.info("plan %s: %d windows, %d ideas, %d grounded, %d candidates; asked %s",
                 plan["id"], len(windows), len(ideas), len(grounded), len(cands), sorted(asked))

    async def _after_evaluation(self, plan: dict, verdicts: dict[str, dict], send: Send) -> None:
        w = plan["work"]
        n_members = len(self.db.member_names())
        scored = score_candidates(w["candidates"], verdicts, n_members, w["priority"], w["counts"],
                                  w["weather"])
        top = top_distinct(scored, 3)
        if not top:
            return await self._give_up(
                plan, "Every idea clashed with someone's limits. Try a different time?", send)
        inputs = [build_pitch_input(_public(c), _weather_public(w["weather"][c["window"]]),
                                    _novelty_note(c["category"], w["counts"])) for c in top]
        pitches = await self.llm.pitches(inputs)
        options = [_display_option(c, w, len(w["responded"]), n_members, pitches.get(c["option_id"], ""))
                   for c in top]
        self.db.update_plan(plan["id"], state="voting", options=options,
                            work=w | {"scored_ids": [c["option_id"] for c in scored]})
        plan = self.db.plan(plan["id"])
        log.info("plan %s voting: %s", plan["id"], [(o["option_id"], o["title"]) for o in options])
        msg = self._carousel_message(plan)
        await self._to_planner(plan, msg, send)
        await self._push(plan["planner_sender"], msg, send)

    def _carousel_message(self, plan: dict, hint: str = "") -> ChatMessage:
        label = plan["work"].get("window_label", "your window")
        lines = [hint] if hint else []
        lines.append(f"Here are {len(plan['options'])} options for {label}:")
        for i, o in enumerate(plan["options"], 1):
            lines.append(f"{i}. {o['title']} — {o['when']} at {o['place']}. {o['pitch']}".rstrip())
        votes = self.db.votes(plan["id"])
        lines.append(f"Tap Vote (or reply \"vote 1\"). {len(votes)}/{len(self.db.member_names())} voted.")
        items = [carousel_item(o, plan["id"]) for o in plan["options"]]
        return card_message("\n".join(lines), "carousel",
                            carousel_payload("Pick one", label.capitalize(), items))


# ================================================================ helpers

def _leader(plan: dict, votes: dict[str, str]) -> tuple[dict | None, int]:
    counts = {o["option_id"]: 0 for o in plan["options"]}
    for v in votes.values():
        if v in counts:
            counts[v] += 1
    best = None
    for o in plan["options"]:  # ties go to the higher-ranked option
        if best is None or counts[o["option_id"]] > counts[best["option_id"]]:
            best = o
    return best, (counts[best["option_id"]] if best else 0)


def _window_label(text: str, win: tuple[datetime, datetime]) -> str:
    low = text.lower()
    for phrase in ("this weekend", "next weekend", "tonight", "tomorrow"):
        if phrase in low:
            return phrase
    s, e = win
    if s.date() == e.date():
        return s.strftime("%A")
    return f"{s.strftime('%a %b %-d')} – {e.strftime('%a %b %-d')}"


def _public(c: dict) -> dict:
    return {k: c[k] for k in ("option_id", "title", "category", "start", "end", "place", "indoor",
                              "cost_band", "cost_per_person", "source")}


def _weather_public(w: dict) -> dict:
    return {"rain_likely": w["rain_likely"], "max_precip": w["max_precip"], "temp_c": w["temp_c"]}


def _novelty_note(category: str, counts: dict[str, int]) -> str:
    n = counts.get(category, 0)
    label = category.replace("_", " ")
    return "new for the group" if n == 0 else f"you've done {label} {n}x lately"


def _display_option(c: dict, work: dict, responded: int, n_members: int, pitch: str) -> dict:
    """Public, card-ready option. Aggregates only: no per-member verdicts or reasons."""
    start = parse_iso(c["start"])
    weather = work["weather"][c["window"]]
    wx = "indoor" if c["indoor"] else "outdoor"
    if weather["rain_likely"]:
        rf = parse_iso(weather["rain_from"]) if weather.get("rain_from") else None
        wx += f", rain from {planner.fmt_time(rf)}" if rf and rf > start else ", rain likely"
    elif weather.get("temp_c") is not None:
        wx += f", {weather['temp_c']}°C and dry"
    badges = []
    if c["all_yes"] and c["avail_count"] == responded:
        badges.append({"label": "Works for everyone", "variant": "success"})
    n_seen = work["counts"].get(c["category"], 0)
    if n_seen == 0:
        badges.append({"label": "New for you", "variant": "info"})
    else:
        badges.append({"label": f"You've done this {n_seen}×", "variant": "warning"})
    if c["avail_count"] < n_members:
        badges.append({"label": f"{start.strftime('%A')} works for {c['avail_count']}/{n_members}",
                       "variant": "info"})
    secondary = f"{c['place']} · {c['cost_band']}"
    if c["worst_travel_min"]:
        secondary += f" · max {c['worst_travel_min']} min for anyone"
    return {**_public(c), "when": planner.fmt_when(c["start"]),
            "subtitle": f"{start.strftime('%a')} {planner.fmt_time(start)} · {wx}",
            "secondary_text": secondary, "badges": badges, "pitch": pitch,
            "coming": list(c["free_members"])}
