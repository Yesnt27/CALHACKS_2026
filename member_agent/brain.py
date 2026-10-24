"""Member agent logic, independent of the uAgents runtime.

Owner-only chat; lore-plan messages accepted only from the configured Group agent
(privacy invariant 7). Replies to the Group carry free slots and verdicts only.
"""

import json
import logging
import re
import secrets
import string
from pathlib import Path

from member_agent.calendar_ics import CalendarReader, free_slots
from member_agent.profile import EMPTY_PROFILE, merge_profile, readback, verdict
from shared.config import parse_iso
from shared.geo import geocode
from shared.llm import LLM
from shared.protocol import (AvailabilityReply, AvailabilityRequest, EvaluateReply,
                             EvaluateRequest, Verdict)

log = logging.getLogger("lore.member")

NOT_YOURS = "This is a personal agent."
HELP = ("I'm your private Lore Keeper agent. Tell me your limits in plain words (\"budget $25, no "
        "alcohol, home by 11, max 30 min travel\"). Commands: `calendar <ical url>`, `home <neighborhood>`, "
        "`profile`, `fix: …`, `forget`. I only ever tell the group's agent your free times and a "
        "yes / stretch / no per option — never why.")


def new_pair_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


class MemberBrain:
    def __init__(self, name: str, state_path: str | Path, group_address: str, llm: LLM,
                 calendar: CalendarReader | None = None):
        self.name = name
        self.state_path = Path(state_path)
        self.group_address = group_address
        self.llm = llm
        self.calendar = calendar or CalendarReader()
        self.state = self._load()
        if not self.state.get("owner_sender") and not self.state.get("pair_code"):
            self.state["pair_code"] = new_pair_code()
            self._save()

    # ------------------------------------------------------------ state

    def _load(self) -> dict:
        base = {"owner_sender": None, "pair_code": None, "profile": dict(EMPTY_PROFILE),
                "home": None, "ical_url": None}
        if self.state_path.exists():
            base.update(json.loads(self.state_path.read_text()))
        return base

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2))

    def banner(self, address: str) -> str:
        if self.state.get("owner_sender"):
            return f"Member agent for {self.name} at {address} (paired)."
        return (f"Member agent for {self.name} at {address}. Pair from ASI:One with: "
                f"pair {self.state['pair_code']}")

    def _readback(self) -> str:
        return readback(self.state["profile"], self.state["home"], bool(self.state["ical_url"]))

    # ------------------------------------------------------------ owner chat

    async def on_chat(self, sender: str, text: str) -> list[str]:
        text = text.strip()
        owner = self.state.get("owner_sender")
        if owner is None:
            m = re.match(r"^\s*pair\s+(\S+)\s*$", text, re.I)
            if m and self.state.get("pair_code") and \
                    secrets.compare_digest(m.group(1).upper(), self.state["pair_code"]):
                self.state["owner_sender"] = sender
                self.state["pair_code"] = None
                self._save()
                log.info("paired with owner %s", sender)
                return ["Paired — I'm your private agent now. " + HELP]
            return [NOT_YOURS]
        if sender != owner:
            return [NOT_YOURS]
        if not text:
            return []
        low = text.lower()
        if re.match(r"^(help|\?)$", low):
            return [HELP]
        if low.startswith("pair"):
            return ["Already paired with you."]
        if m := re.match(r"^\s*calendar\s+(\S+)\s*$", text, re.I):
            return [await self._set_calendar(m.group(1))]
        if m := re.match(r"^\s*home\s+(.+?)\s*$", text, re.I):
            return [await self._set_home(m.group(1))]
        if low in ("profile", "show profile", "my profile"):
            return [self._readback()]
        if low in ("forget", "forget me", "forget everything"):
            self.state.update(profile=dict(EMPTY_PROFILE), home=None, ical_url=None)
            self._save()
            return ["Done — I've cleared your limits, home area and calendar link."]
        replace = False
        if m := re.match(r"^\s*fix\s*:\s*(.+)$", text, re.I | re.S):
            text, replace = m.group(1), True
        parsed = await self.llm.constraints(text)
        if not parsed:
            return ["I didn't catch any limits. Try: \"budget $25, no alcohol, home by 11, "
                    "max 30 min travel\"."]
        self.state["profile"] = merge_profile(self.state["profile"], parsed, replace_other=replace)
        self._save()
        return [self._readback()]

    async def _set_calendar(self, url: str) -> str:
        try:
            await self.calendar.raw(url)
        except Exception as exc:
            log.info("calendar validation failed: %s", type(exc).__name__)
            return "I couldn't read that calendar. Paste the secret iCal (.ics) address."
        self.state["ical_url"] = url
        self._save()
        return "Got it. I only read busy times, never event titles."

    async def _set_home(self, name: str) -> str:
        hit = await geocode(name)
        if not hit:
            return f"I couldn't find \"{name}\". Try a neighborhood or city name."
        self.state["home"] = hit
        self._save()
        return f"Home area set to {hit['label']} (rounded to about 1 km). Only travel minutes are shared."

    # ------------------------------------------------------------ lore-plan

    def _from_group(self, sender: str) -> bool:
        ok = bool(self.group_address) and sender == self.group_address
        if not ok:
            log.warning("ignoring lore-plan message from non-group sender %s", sender)
        return ok

    async def on_availability(self, sender: str, req: AvailabilityRequest) -> AvailabilityReply | None:
        if not self._from_group(sender):
            return None
        ws, we = parse_iso(req.window_start), parse_iso(req.window_end)
        busy = await self.calendar.busy(self.state.get("ical_url"), ws, we)
        slots = free_slots(busy, ws, we, self.state["profile"].get("latest_end"))
        log.info("availability for %s: %d free slots (calendar=%s)", req.plan_id, len(slots),
                 busy is not None)
        return AvailabilityReply(plan_id=req.plan_id, free_slots=slots, has_calendar=busy is not None)

    async def on_evaluate(self, sender: str, req: EvaluateRequest) -> EvaluateReply | None:
        if not self._from_group(sender):
            return None
        busy = []
        if req.options:
            starts = [parse_iso(o.start) for o in req.options]
            ends = [parse_iso(o.end) for o in req.options]
            busy = await self.calendar.busy(self.state.get("ical_url"), min(starts), max(ends)) or []
        out = []
        for opt in req.options:
            fit, travel, reason = verdict(opt, self.state["profile"], self.state["home"], busy)
            log.debug("verdict %s: %s (%s)", opt.option_id, fit, reason)  # owner's terminal only
            out.append(Verdict(option_id=opt.option_id, fit=fit, travel_min=int(travel)))
        return EvaluateReply(plan_id=req.plan_id, verdicts=out)
