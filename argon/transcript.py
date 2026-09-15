"""The append-only log that is Argon's entire state.

Every message he sends, every message Argon sends, every tool call, every
sensed event and every tick appends one row here.  Nothing else is
authoritative.  The agent's context is literally the last two days of this
table rendered in order.

Three things follow from that, and they are the whole reason the design is
shaped this way:

**The prompt cache works by construction.**  Rows are immutable and only ever
appended, so yesterday seals at midnight and today only grows at the end.  A
context built by walking rows oldest-first is byte-stable across ticks, which is
what makes a five-minute cadence cost $4 a month instead of $11.  The previous
system re-rendered a digest on every call and would have missed cache every
time.

**Restraint stops being a gate.**  The agent can see that it spoke at 18:05 and
that he has not replied since, because both facts are rows.  The old
``max_per_day`` counter existed because the model could not see its own outbox;
once it can, the counter is redundant and the thing it was approximating —
judgement about whether saying more is welcome — is available directly.

**It replays.**  "Why did it say that" is a read of this table, not an
archaeology dig across DailyState, the task board, the daily log and phone
state, any one of which could disagree with the others.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argon import clock

#: Rows the agent renders as conversation turns.  Anything else is a fact it
#: observes rather than something that was said.
SPOKEN = {"message_in", "message_out"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    day     TEXT NOT NULL,
    kind    TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_day ON events (day, seq);
"""


@dataclass(frozen=True)
class Event:
    seq: int
    at: str
    day: str
    kind: str
    payload: dict[str, Any]


class Transcript:
    """Append-only event store.

    ponytail: one connection behind one lock.  Writes are a few per minute and
    reads are one per tick, so contention is not real; move to a connection pool
    only if a profile says so.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # WAL so the HTTP thread can append while the agent loop reads.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def append(self, kind: str, **payload: Any) -> int:
        """Record one thing that happened.  Returns its sequence number."""
        when = clock.now()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events (at, day, kind, payload) VALUES (?, ?, ?, ?)",
                (when.isoformat(), clock.day_key(when), kind, json.dumps(payload, default=str)),
            )
            self._db.commit()
            return int(cur.lastrowid)

    def window(self, days: int = 2) -> list[Event]:
        """Every row from the last *days* local days, oldest first.

        Oldest first is not cosmetic: it is the order the cached prefix is built
        in, and reversing it would invalidate the cache on every tick.
        """
        keys = clock.recent_days(days)
        marks = ",".join("?" * len(keys))
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM events WHERE day IN ({marks}) ORDER BY seq", keys
            ).fetchall()
        return [_row(r) for r in rows]

    def since(self, seq: int) -> list[Event]:
        """Rows appended after *seq*, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE seq > ? ORDER BY seq", (seq,)
            ).fetchall()
        return [_row(r) for r in rows]

    def last(self, kind: str) -> Event | None:
        """The most recent row of one kind, or None."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM events WHERE kind = ? ORDER BY seq DESC LIMIT 1", (kind,)
            ).fetchone()
        return _row(row) if row else None


def _row(r: sqlite3.Row) -> Event:
    return Event(seq=r["seq"], at=r["at"], day=r["day"], kind=r["kind"],
                 payload=json.loads(r["payload"]))


def _selftest() -> None:
    """Run with ``python -m argon.transcript``."""
    import tempfile
    from datetime import datetime

    with tempfile.TemporaryDirectory() as tmp:
        t = Transcript(Path(tmp) / "t.db")

        clock.set_for_test(datetime(2026, 9, 13, 21, 0, tzinfo=clock.TZ))
        t.append("message_in", text="yesterday")
        clock.set_for_test(datetime(2026, 9, 14, 18, 5, tzinfo=clock.TZ))
        a = t.append("message_out", text="today")
        t.append("tick")

        win = [e.payload.get("text") for e in t.window(2)]
        assert win == ["yesterday", "today", None], win
        # A one-day window must drop yesterday entirely.
        assert [e.payload.get("text") for e in t.window(1)] == ["today", None]
        # Ordering is oldest-first and stable — the cache depends on it.
        assert [e.seq for e in t.window(2)] == sorted(e.seq for e in t.window(2))
        assert [e.kind for e in t.since(a)] == ["tick"]
        assert t.last("message_out").payload["text"] == "today"
        assert t.last("nothing") is None

        # An older day falling out of the window must not renumber anything.
        clock.set_for_test(datetime(2026, 9, 15, 9, 0, tzinfo=clock.TZ))
        assert [e.payload.get("text") for e in t.window(2)] == ["today", None]

        clock.set_for_test(None)
    print("transcript selftest ok")


if __name__ == "__main__":
    _selftest()
