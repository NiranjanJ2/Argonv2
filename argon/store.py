"""Mutable records: the task board and durable facts.

The transcript is the history; this is the present.  Two stores rather than one
because they answer different questions — "what happened" is append-only and
"what is still true" is not, and deriving the second from the first on every
tick would mean replaying the log to answer "what is due".

Every mutation here also appends a transcript event, so the agent sees task
changes in its context without being handed a separate digest.  That is the
rule that keeps the two from drifting: **state changes are events too.**
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from argon import clock
from argon.transcript import Transcript

#: `until` comes from the model as free text. "tomorrow" sorts above every real
#: date, so a fact set to expire never did. Anything not an ISO date is dropped
#: and the fact is simply durable, which is the safer of the two mistakes.
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    subject    TEXT,
    due        TEXT,
    priority   TEXT NOT NULL DEFAULT 'normal',
    done       INTEGER NOT NULL DEFAULT 0,
    done_at    TEXT,
    started_at TEXT,
    source     TEXT NOT NULL DEFAULT 'him',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id         TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    standing   INTEGER NOT NULL DEFAULT 0,
    until      TEXT,
    created_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    subject: str | None
    due: str | None
    priority: str
    done: bool
    source: str
    started_at: str | None = None

    @property
    def started(self) -> bool:
        return self.started_at is not None

    def line(self) -> str:
        bits = [self.title]
        if self.subject:
            bits.append(f"({self.subject})")
        if self.due:
            bits.append(f"due {self.due}")
        if self.priority != "normal":
            bits.append(f"[{self.priority}]")
        return " ".join(bits)


class Store:
    def __init__(self, path: Path, transcript: Transcript) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()
        self._t = transcript

    # -- tasks ------------------------------------------------------------
    def add_task(self, title: str, *, subject: str = "", due: str = "",
                 priority: str = "normal", source: str = "him") -> Task:
        tid = uuid.uuid4().hex[:12]
        with self._lock:
            self._db.execute(
                "INSERT INTO tasks (id,title,subject,due,priority,source,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (tid, title, subject or None, due or None, priority, source,
                 clock.now().isoformat()),
            )
            self._db.commit()
        self._t.append("task_added", id=tid, summary=f"{title}"
                       + (f" due {due}" if due else "") + f" (from {source})")
        return self.task(tid)  # type: ignore[return-value]

    def task(self, tid: str) -> Task | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return _task(row) if row else None

    def tasks(self, *, done: bool = False) -> list[Task]:
        """Open tasks by due date, undated last.  Deterministic ordering matters:
        the board is rendered into a cached prefix and a reshuffle costs money."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE done=? ORDER BY due IS NULL, due, created_at",
                (1 if done else 0,),
            ).fetchall()
        return [_task(r) for r in rows]

    def start_task(self, tid: str) -> Task | None:
        """He has begun this. Only he can say so — never inferred from a clock.

        The guard is inside the same lock as the write: reading first and
        writing second let two concurrent calls both pass the check and append
        two events for one action.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tasks WHERE id=? AND done=0 AND started_at IS NULL",
                (tid,)).fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE tasks SET started_at=? WHERE id=?",
                             (clock.now().isoformat(), tid))
            self._db.commit()
            title = row["title"]
        self._t.append("task_started", id=tid, summary=title)
        return self.task(tid)

    def complete_task(self, tid: str) -> Task | None:
        """Mark done. Guard and write share one lock, so two concurrent
        completes cannot both pass and log the task twice."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tasks WHERE id=? AND done=0", (tid,)).fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE tasks SET done=1, done_at=? WHERE id=?",
                             (clock.now().isoformat(), tid))
            self._db.commit()
            title = row["title"]
        self._t.append("task_done", id=tid, summary=title)
        return self.task(tid)

    def update_task(self, tid: str, **changes: Any) -> Task | None:
        allowed = {k: v for k, v in changes.items()
                   if k in {"title", "subject", "due", "priority"} and v is not None}
        if not allowed or self.task(tid) is None:
            return None
        sets = ", ".join(f"{k}=?" for k in allowed)
        with self._lock:
            self._db.execute(f"UPDATE tasks SET {sets} WHERE id=?",
                             (*allowed.values(), tid))
            self._db.commit()
        self._t.append("task_updated", id=tid,
                       summary=", ".join(f"{k}={v}" for k, v in allowed.items()))
        return self.task(tid)

    def find_task(self, needle: str) -> Task | None:
        """Loose match on title, for when he names a task instead of an id."""
        needle = needle.strip().lower()
        for t in self.tasks():
            if needle == t.id or needle in t.title.lower():
                return t
        return None

    # -- facts ------------------------------------------------------------
    def remember(self, text: str, *, standing: bool = False, until: str = "") -> str:
        fid = uuid.uuid4().hex[:12]
        if until and not _ISO_DAY.match(until.strip()):
            self._t.append("bad_until", summary=f"ignored until={until!r} on {text[:60]}")
            until = ""
        with self._lock:
            self._db.execute(
                "INSERT INTO facts (id,text,standing,until,created_at) VALUES (?,?,?,?,?)",
                (fid, text, 1 if standing else 0, until or None, clock.now().isoformat()),
            )
            self._db.commit()
        self._t.append("remembered", id=fid, summary=text)
        return fid

    def facts(self) -> list[str]:
        """Live facts, standing ones first.  Expired ones are filtered, not deleted."""
        today = clock.day_key()
        with self._lock:
            rows = self._db.execute(
                "SELECT text, standing, until FROM facts ORDER BY standing DESC, created_at"
            ).fetchall()
        return [r["text"] for r in rows if not r["until"] or r["until"] >= today]


    # -- decisions ---------------------------------------------------------
    def set_quiet(self, until: datetime, reason: str) -> None:
        """Record that Argon has committed to silence until *until*.

        This is the model's decision, not a rule imposed on it. Python's only
        job is to remember it — without somewhere to put the commitment, the
        agent re-derived it from scratch every tick and announced "standing
        down" four times in two hours.
        """
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('quiet', ?)",
                (json.dumps({"until": until.isoformat(), "reason": reason}),))
            self._db.commit()
        self._t.append("stood_down", summary=f"until {until:%H:%M} — {reason}"[:200])

    def quiet(self) -> tuple[datetime, str] | None:
        """The live commitment to silence, or None once it has lapsed."""
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM settings WHERE key='quiet'").fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row["value"])
            until = datetime.fromisoformat(data["until"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
        return (until, data.get("reason", "")) if until > clock.now() else None

    def clear_quiet(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM settings WHERE key='quiet'")
            self._db.commit()
        self._t.append("stand_down_cleared")


def _task(row: sqlite3.Row) -> Task:
    return Task(id=row["id"], title=row["title"], subject=row["subject"],
                due=row["due"], priority=row["priority"], done=bool(row["done"]),
                source=row["source"], started_at=row["started_at"])


def _selftest() -> None:
    import tempfile
    from datetime import datetime

    with tempfile.TemporaryDirectory() as tmp:
        clock.set_for_test(datetime(2026, 9, 14, 18, 0, tzinfo=clock.TZ))
        t = Transcript(Path(tmp) / "t.db")
        s = Store(Path(tmp) / "s.db", t)

        a = s.add_task("AP Chem pset", subject="Chem", due="2026-09-16")
        b = s.add_task("Read Ch 3", source="classroom")
        s.add_task("No due date")

        # Dated first and in date order; undated last. The prefix depends on it.
        assert [x.title for x in s.tasks()][:2] == ["AP Chem pset", "Read Ch 3"] or \
               [x.title for x in s.tasks()][0] == "AP Chem pset"
        assert s.tasks()[-1].title == "No due date"

        assert s.find_task("chem").id == a.id
        assert s.find_task(b.id).title == "Read Ch 3"
        assert s.find_task("nonsense") is None

        assert s.complete_task(a.id).done is True
        assert s.complete_task(a.id) is None, "completing twice is not an event"
        assert a.id not in [x.id for x in s.tasks()]
        assert s.update_task(b.id, priority="high").priority == "high"
        assert s.update_task(b.id, bogus="x") is None, "unknown fields change nothing"

        assert s.start_task(b.id).started is True
        assert s.start_task(b.id) is None, "starting twice is not an event"
        assert s.tasks()[0].started_at is not None or s.find_task("Read Ch 3").started

        s.remember("practice Tuesdays", standing=True)
        s.remember("lab meeting moved", until="2026-09-01")   # expired
        s.remember("essay due soon", until="2026-12-01")
        # Free text sorts above any real date, so it would never expire.
        s.remember("vague deadline", until="tomorrow")
        assert s.facts() == ["practice Tuesdays", "essay due soon", "vague deadline"], s.facts()
        assert "bad_until" in [e.kind for e in t.window(2)]

        # Every mutation is visible in the transcript, so the agent sees it.
        kinds = [e.kind for e in t.window(2)]
        assert kinds.count("task_added") == 3
        assert "task_done" in kinds and "remembered" in kinds and "task_updated" in kinds
        assert "task_started" in kinds

        # Concurrent completes must produce exactly one event.
        import threading

        race = s.add_task("raced")
        gate = threading.Barrier(6)
        results: list = []

        def finish() -> None:
            gate.wait()
            results.append(s.complete_task(race.id))

        workers = [threading.Thread(target=finish) for _ in range(6)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        assert sum(r is not None for r in results) == 1, results
        assert [e.payload.get("summary") for e in t.window(2)
                if e.kind == "task_done"].count("raced") == 1

        # A commitment to silence outlives the turn that made it, and lapses
        # on its own without anyone having to clear it.
        assert s.quiet() is None
        s.set_quiet(clock.now() + timedelta(hours=2), "he asked me to stand down")
        live = s.quiet()
        assert live and "stand down" in live[1]
        assert "stood_down" in [e.kind for e in t.window(2)]

        clock.set_for_test(datetime(2026, 9, 15, 4, 0, tzinfo=clock.TZ))
        assert s.quiet() is None, "it must lapse on its own"

        clock.set_for_test(datetime(2026, 9, 14, 18, 0, tzinfo=clock.TZ))
        assert s.quiet() is not None, "and hold until it does"
        s.clear_quiet()
        assert s.quiet() is None

        clock.set_for_test(None)
    print("store selftest ok")


if __name__ == "__main__":
    _selftest()
