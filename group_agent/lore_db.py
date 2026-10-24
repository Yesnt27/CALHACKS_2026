"""SQLite lore + plan state for the Group agent.

Lore never stores verdicts. plan_replies is transient and purged when a plan
locks or cancels (privacy invariant 8).
"""

import json
import sqlite3
from collections import Counter
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
  name TEXT PRIMARY KEY,
  member_agent_address TEXT NOT NULL,
  asi_sender TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS hangouts (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  title TEXT NOT NULL,
  categories TEXT NOT NULL,          -- JSON array
  place TEXT,
  attendees TEXT NOT NULL,           -- JSON array of member names
  recap TEXT,
  quotes TEXT,                       -- JSON array of {"text": ..., "speaker": ...}, verbatim
  status TEXT NOT NULL DEFAULT 'done' -- planned | done
);
CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY,               -- p_xxxx
  planner_sender TEXT NOT NULL,
  window_start TEXT, window_end TEXT,
  state TEXT NOT NULL,               -- collecting_availability | evaluating | voting | locked | cancelled
  deadline TEXT,
  options_json TEXT,                 -- the 3 options shown (public fields only)
  chosen_option TEXT,
  created_at TEXT NOT NULL,
  planner_session TEXT,              -- chat session of the plan request (final card goes there)
  work_json TEXT                     -- transient pipeline scratch (windows, candidates); cleared on lock/cancel
);
CREATE TABLE IF NOT EXISTS plan_replies (  -- transient; purged when a plan locks or cancels
  plan_id TEXT, member TEXT, kind TEXT, payload_json TEXT,
  PRIMARY KEY (plan_id, member, kind)
);
CREATE TABLE IF NOT EXISTS votes (
  plan_id TEXT, member TEXT, option_id TEXT,
  PRIMARY KEY (plan_id, member)
);
"""

OPEN_STATES = ("collecting_availability", "evaluating", "voting")


def _hangout(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["categories"] = json.loads(d["categories"] or "[]")
    d["attendees"] = json.loads(d["attendees"] or "[]")
    d["quotes"] = json.loads(d["quotes"] or "[]")
    return d


class LoreDB:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------ members

    def sync_members(self, members: list[dict]) -> None:
        """Upsert members.json rows; keeps existing asi_sender bindings."""
        for m in members:
            self.conn.execute(
                "INSERT INTO members(name, member_agent_address) VALUES(?, ?) "
                "ON CONFLICT(name) DO UPDATE SET member_agent_address=excluded.member_agent_address",
                (m["name"], m["member_agent_address"]))
        self.conn.commit()

    def member_addresses(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT name, member_agent_address FROM members ORDER BY name")
        return {r["name"]: r["member_agent_address"] for r in rows}

    def member_names(self) -> list[str]:
        return list(self.member_addresses())

    def member_for_address(self, address: str) -> str | None:
        r = self.conn.execute("SELECT name FROM members WHERE member_agent_address=?",
                              (address,)).fetchone()
        return r["name"] if r else None

    def member_for_sender(self, sender: str) -> str | None:
        r = self.conn.execute("SELECT name FROM members WHERE asi_sender=?", (sender,)).fetchone()
        return r["name"] if r else None

    def sender_for_member(self, name: str) -> str | None:
        r = self.conn.execute("SELECT asi_sender FROM members WHERE name=?", (name,)).fetchone()
        return r["asi_sender"] if r else None

    def find_member(self, name: str) -> str | None:
        r = self.conn.execute("SELECT name FROM members WHERE lower(name)=lower(?)",
                              (name.strip(),)).fetchone()
        return r["name"] if r else None

    def bind_sender(self, name: str, sender: str) -> None:
        self.conn.execute("UPDATE members SET asi_sender=NULL WHERE asi_sender=?", (sender,))
        self.conn.execute("UPDATE members SET asi_sender=? WHERE name=?", (sender, name))
        self.conn.commit()

    # ------------------------------------------------------------ hangouts

    def add_hangout(self, date: str, title: str, categories: list[str], place: str | None,
                    attendees: list[str], recap: str | None = None,
                    quotes: list[dict] | None = None, status: str = "done") -> int:
        cur = self.conn.execute(
            "INSERT INTO hangouts(date, title, categories, place, attendees, recap, quotes, status) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (date, title, json.dumps(categories), place, json.dumps(attendees), recap,
             json.dumps(quotes or [], ensure_ascii=False), status))
        self.conn.commit()
        return cur.lastrowid

    def hangout(self, hid: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM hangouts WHERE id=?", (hid,)).fetchone()
        return _hangout(r) if r else None

    def hangouts(self, where: str = "1=1", params: tuple = (), limit: int = 50,
                 order: str = "date DESC") -> list[dict]:
        rows = self.conn.execute(f"SELECT * FROM hangouts WHERE {where} ORDER BY {order} LIMIT ?",
                                 (*params, limit))
        return [_hangout(r) for r in rows]

    def recent_hangouts(self, n: int = 10) -> list[dict]:
        return self.hangouts(limit=n)

    def category_counts(self, n: int = 10) -> dict[str, int]:
        c: Counter = Counter()
        for h in self.recent_hangouts(n):
            c.update(set(h["categories"]))
        return dict(c)

    def priority_members(self) -> list[str]:
        """Members who missed >= 2 of the last 3 done hangouts (quiet re-inclusion)."""
        last3 = self.hangouts("status='done'", limit=3)
        if len(last3) < 2:
            return []
        out = []
        for name in self.member_names():
            missed = sum(1 for h in last3 if name not in h["attendees"])
            if missed >= 2:
                out.append(name)
        return out

    def latest_for_recap(self, today_iso: str) -> dict | None:
        rows = self.hangouts("date <= ?", (today_iso[:10] + "T99",), limit=1)
        return rows[0] if rows else (self.hangouts(limit=1) or [None])[0]

    def update_recap(self, hid: int, recap: str, quotes: list[dict],
                     attendees: list[str] | None) -> None:
        h = self.hangout(hid)
        merged = (h["quotes"] if h else []) + quotes
        if attendees:
            self.conn.execute("UPDATE hangouts SET attendees=? WHERE id=?",
                              (json.dumps(attendees), hid))
        self.conn.execute(
            "UPDATE hangouts SET recap=?, quotes=?, status='done' WHERE id=?",
            (recap, json.dumps(merged, ensure_ascii=False), hid))
        self.conn.commit()

    def add_quotes(self, hid: int, quotes: list[dict], limit: int = 3) -> int:
        """Append up to `limit` quotes whose text isn't stored yet; returns how many were added."""
        h = self.hangout(hid)
        if h is None:
            return 0
        seen = {q["text"] for q in h["quotes"]}
        new = []
        for q in quotes:
            if q["text"] not in seen and len(new) < limit:
                seen.add(q["text"])
                new.append(q)
        if new:
            self.conn.execute("UPDATE hangouts SET quotes=? WHERE id=?",
                              (json.dumps(h["quotes"] + new, ensure_ascii=False), hid))
            self.conn.commit()
        return len(new)

    def load_seed(self, path: str | Path, reset: bool = False) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if reset:
            self.conn.execute("DELETE FROM hangouts")
        elif self.conn.execute("SELECT COUNT(*) FROM hangouts").fetchone()[0]:
            return 0
        for h in data:
            self.add_hangout(h["date"], h["title"], h["categories"], h.get("place"),
                             h.get("attendees", []), h.get("recap"), h.get("quotes"),
                             h.get("status", "done"))
        return len(data)

    # ------------------------------------------------------------ plans

    def create_plan(self, plan_id: str, planner_sender: str, window_start: str, window_end: str,
                    deadline: str, created_at: str, planner_session: str | None) -> None:
        self.conn.execute(
            "INSERT INTO plans(id, planner_sender, window_start, window_end, state, deadline, "
            "created_at, planner_session) VALUES(?, ?, ?, ?, 'collecting_availability', ?, ?, ?)",
            (plan_id, planner_sender, window_start, window_end, deadline, created_at,
             planner_session))
        self.conn.commit()

    def plan(self, plan_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["options"] = json.loads(d["options_json"]) if d["options_json"] else []
        d["work"] = json.loads(d["work_json"]) if d["work_json"] else {}
        return d

    def update_plan(self, plan_id: str, **fields) -> None:
        if "options" in fields:
            fields["options_json"] = json.dumps(fields.pop("options"), ensure_ascii=False)
        if "work" in fields:
            w = fields.pop("work")
            fields["work_json"] = None if w is None else json.dumps(w, ensure_ascii=False)
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE plans SET {cols} WHERE id=?", (*fields.values(), plan_id))
        self.conn.commit()

    def plans_in(self, *states: str) -> list[dict]:
        q = ",".join("?" for _ in states)
        rows = self.conn.execute(f"SELECT id FROM plans WHERE state IN ({q}) ORDER BY created_at",
                                 states)
        return [self.plan(r["id"]) for r in rows]

    def current_plan(self) -> dict | None:
        """Most recent plan that is still open (pull-based voting reads this)."""
        rows = self.plans_in(*OPEN_STATES)
        return rows[-1] if rows else None

    def finish_plan(self, plan_id: str, state: str, chosen_option: str | None = None) -> None:
        """Lock or cancel: purge transient replies and pipeline scratch."""
        self.update_plan(plan_id, state=state, chosen_option=chosen_option, work=None)
        self.conn.execute("DELETE FROM plan_replies WHERE plan_id=?", (plan_id,))
        self.conn.commit()

    # ------------------------------------------------------------ replies & votes

    def put_reply(self, plan_id: str, member: str, kind: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO plan_replies(plan_id, member, kind, payload_json) VALUES(?,?,?,?)",
            (plan_id, member, kind, json.dumps(payload)))
        self.conn.commit()

    def replies(self, plan_id: str, kind: str) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT member, payload_json FROM plan_replies WHERE plan_id=? AND kind=?",
            (plan_id, kind))
        return {r["member"]: json.loads(r["payload_json"]) for r in rows}

    def count_replies(self, plan_id: str | None = None) -> int:
        if plan_id is None:
            return self.conn.execute("SELECT COUNT(*) FROM plan_replies").fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM plan_replies WHERE plan_id=?",
                                 (plan_id,)).fetchone()[0]

    def upsert_vote(self, plan_id: str, member: str, option_id: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO votes(plan_id, member, option_id) VALUES(?,?,?)",
                          (plan_id, member, option_id))
        self.conn.commit()

    def votes(self, plan_id: str) -> dict[str, str]:
        rows = self.conn.execute("SELECT member, option_id FROM votes WHERE plan_id=?", (plan_id,))
        return {r["member"]: r["option_id"] for r in rows}
