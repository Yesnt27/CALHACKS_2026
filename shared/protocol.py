"""lore-plan protocol: the only messages exchanged between Group and Member agents.

Member replies carry ids, ISO times, enums, ints and bools only (privacy invariant 1).
"""

from uagents import Model, Protocol


class OptionSpec(Model):
    option_id: str
    start: str               # ISO 8601 with offset
    end: str
    cost_per_person: float   # estimate in USD
    alcohol_centric: bool
    late_night: bool         # ends after 23:00
    lat: float
    lng: float
    indoor: bool


class AvailabilityRequest(Model):
    plan_id: str
    window_start: str
    window_end: str


class AvailabilityReply(Model):
    plan_id: str
    free_slots: list[list[str]]   # [[start, end], ...] — no titles, no reasons
    has_calendar: bool


class EvaluateRequest(Model):
    plan_id: str
    options: list[OptionSpec]


class Verdict(Model):
    option_id: str
    fit: str                 # "yes" | "stretch" | "no"
    travel_min: int


class EvaluateReply(Model):
    plan_id: str
    verdicts: list[Verdict]


FITS = ("yes", "stretch", "no")

PLAN_PROTO_NAME = "lore-plan"
PLAN_PROTO_VERSION = "0.1.0"


def new_plan_proto() -> Protocol:
    """A fresh Protocol instance, so Group and Member agents can share a process (Bureau, tests)."""
    return Protocol(name=PLAN_PROTO_NAME, version=PLAN_PROTO_VERSION)


plan_proto = new_plan_proto()
