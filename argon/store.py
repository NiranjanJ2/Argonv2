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
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from argon import clock
from argon.transcript import Transcript, _secure

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
    external_id TEXT,
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


#: How long a start stays current. From a 16:00 start this expires at 04:00,
#: so an evening session survives midnight and nothing survives to the next day.
STARTED_TTL = timedelta(hours=12)


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    subject: str | None
    due: str | None
    priority: str
    done: bool
    source: str
    external_id: str | None = None
    started_at: str | None = None

    @property
    def started(self) -> bool:
        """Whether he is *currently* on this, not whether he ever began it.

        Nothing ever clears started_at: he starts an essay Monday at 16:00,
        gets pulled away, and on Thursday the board still says "working on it"
        and /v1/status still reports mode=working. The agent then builds a whole
        turn around a session that ended three days ago.

        A rolling window rather than a calendar day because he does homework at
        23:40 and midnight must not end the session under him.

        ponytail: a fixed window, not a real session with an end event. Add
        stop_task if he ever wants paused/resumed distinguished from stale.
        """
        if self.started_at is None:
            return False
        try:
            began = datetime.fromisoformat(self.started_at)
        except ValueError:
            return False
        return (clock.now() - began) < STARTED_TTL


    def line(self) -> str:
        """One line for the prompt and the board.

        The weekday and the overdue count are computed here rather than left
        to the model. v1 learned this the hard way: asked what was on his week
        it answered '08/12 Mon', '08/14 Sat', '08/16 Mon' — the dates right and
        three of four weekdays invented. A confidently wrong weekday is worse
        than none, because he plans around it. Same for lateness: stated beats
        derived, and 'due 2026-08-25' made the model do sixty date
        subtractions to find the overdue set.
        """
        bits = [self.title]
        if self.subject:
            bits.append(f"({self.subject})")
        if self.due:
            bits.append(f"due {_due_phrase(self.due)}")
        if self.priority != "normal":
            bits.append(f"[{self.priority}]")
        return " ".join(bits)


class Store:
    def __init__(self, path: Path, transcript: Transcript) -> None:
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        # Checkpoint often. The default only folds the WAL back at 1000 pages,
        # so a long-running gateway sat on a 2 MB WAL in front of a 16 KB
        # database — more of his life at rest than necessary, and a slower
        # recovery if the process is killed.
        self._db.execute("PRAGMA wal_autocheckpoint=64")
        self._db.executescript(SCHEMA)
        self._migrate()
        self._db.commit()
        _secure(path)
        self._t = transcript

    #: Columns added after the first release. `CREATE TABLE IF NOT EXISTS`
    #: leaves an existing table alone, so a new column has to be added by hand
    #: — and an index over it must wait until it exists, or the whole schema
    #: script fails and the service will not start.
    MIGRATIONS = (
        ("tasks", "external_id", "TEXT"),
        # Who closed it. Classroom re-import may undo its own wrong closure but
        # must never undo his, and without recording the difference the two are
        # indistinguishable the moment the row is written.
        ("tasks", "done_by", "TEXT"),
    )

    def _migrate(self) -> None:
        for table, column, kind in self.MIGRATIONS:
            existing = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        self._db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS tasks_external ON tasks (external_id)"
            " WHERE external_id IS NOT NULL")

    # -- tasks ------------------------------------------------------------
    def add_task(self, title: str, *, subject: str = "", due: str = "",
                 priority: str = "normal", source: str = "him",
                 external_id: str = "") -> Task:
        """Add a task. An *external_id* makes it idempotent, so importing the
        same Classroom assignment twice updates it rather than duplicating."""
        if external_id:
            existing = self.task_by_external(external_id)
            if existing is not None:
                if existing.done and self._closed_by(existing.id) != "him":
                    # The source still says he owes this, so a previous
                    # closure was wrong. Without this a task closed by mistake
                    # could never come back: the import matched the completed
                    # row and only updated it.
                    #
                    # Never when *he* closed it. Classroom keeps listing work
                    # he has done but not submitted — a reading, a corrections
                    # sheet handed in on paper — so an unconditional reopen
                    # walked back every checkmark he made, twenty of them in
                    # one evening, each one a PATCH the server answered 200.
                    self.reopen_task(existing.id)
                return self.update_task(existing.id, due=due or None,
                                        title=title, subject=subject or None) \
                    or self.task(existing.id) or existing
        tid = uuid.uuid4().hex[:12]
        with self._lock:
            self._db.execute(
                "INSERT INTO tasks (id,title,subject,due,priority,source,"
                "external_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (tid, title, subject or None, due or None, priority, source,
                 external_id or None, clock.now().isoformat()),
            )
            self._db.commit()
        self._t.append("task_added", id=tid, summary=f"{title}"
                       + (f" due {due}" if due else "") + f" (from {source})")
        return self.task(tid)  # type: ignore[return-value]

    def task(self, tid: str) -> Task | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return _task(row) if row else None

    def task_by_external(self, external_id: str) -> Task | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE external_id=?",
                                   (external_id,)).fetchone()
        return _task(row) if row else None

    def external_ids(self, *, source: str) -> dict[str, Task]:
        """Open tasks from one source, keyed by their external id."""
        return {t.external_id: t for t in self.tasks()
                if t.source == source and t.external_id}

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

    def stop_task(self, tid: str) -> Task | None:
        """He put it down without finishing it. Clears the start, keeps the task.

        Distinct from completing it on purpose: "I am not working on this any
        more" and "this is done" are different claims, and only he can make
        either. Without a stop, the only way out of a started task was to
        finish it, which is how a task started on Tuesday was still "in
        progress" on Friday.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tasks WHERE id=? AND started_at IS NOT NULL",
                (tid,)).fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE tasks SET started_at=NULL WHERE id=?", (tid,))
            self._db.commit()
            title = row["title"]
        self._t.append("task_stopped", id=tid, summary=title)
        return self.task(tid)

    def complete_task(self, tid: str, *, by: str = "him") -> Task | None:
        """Mark done. Guard and write share one lock, so two concurrent
        completes cannot both pass and log the task twice.

        *by* is "him" for a tap or a word, "sync" when Classroom stopped
        listing the work. Only a sync closure may be undone by a later sync —
        see add_task.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tasks WHERE id=? AND done=0", (tid,)).fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE tasks SET done=1, done_at=?, done_by=? WHERE id=?",
                             (clock.now().isoformat(), by, tid))
            self._db.commit()
            title = row["title"]
        self._t.append("task_done", id=tid, summary=title)
        return self.task(tid)

    def _closed_by(self, tid: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT done_by FROM tasks WHERE id=?", (tid,)).fetchone()
        return row["done_by"] if row else None

    def reopen_task(self, tid: str) -> Task | None:
        """Undo a completion. Used when a source still reports work as owed."""
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id=? AND done=1",
                                   (tid,)).fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE tasks SET done=0, done_at=NULL WHERE id=?", (tid,))
            self._db.commit()
            title = row["title"]
        self._t.append("task_reopened", id=tid, summary=title)
        return self.task(tid)

    def update_task(self, tid: str, **changes: Any) -> Task | None:
        """Change a task. A change that changes nothing writes nothing.

        The Classroom sync re-imports every assignment every half hour, so
        without this it appended a `task_updated` event per assignment per
        sync — 394 of them in one night. Those land in the two-day context the
        model reads and are paid for on every tick, which makes a no-op import
        genuinely expensive.
        """
        current = self.task(tid)
        if current is None:
            return None
        allowed = {k: v for k, v in changes.items()
                   if k in {"title", "subject", "due", "priority"} and v is not None
                   and getattr(current, k) != v}
        if not allowed:
            return current
        sets = ", ".join(f"{k}=?" for k in allowed)
        with self._lock:
            self._db.execute(f"UPDATE tasks SET {sets} WHERE id=?",
                             (*allowed.values(), tid))
            self._db.commit()
        self._t.append("task_updated", id=tid,
                       summary=", ".join(f"{k}={v}" for k, v in allowed.items()))
        return self.task(tid)

    def find_tasks(self, needle: str) -> list[Task]:
        """Every open task matching *needle*; an exact id match wins alone."""
        needle = needle.strip().lower()
        exact = [t for t in self.tasks() if needle == t.id]
        if exact:
            return exact
        return [t for t in self.tasks() if needle in t.title.lower()]

    def find_task(self, needle: str) -> Task | None:
        """The single match, or None when there is none *or several*.

        Returning the first of several is the guess that silently completed
        the wrong 'Math homework' in v1. Callers that can report ambiguity
        should use `find_tasks`.
        """
        found = self.find_tasks(needle)
        return found[0] if len(found) == 1 else None

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

    def set_lock(self, until: datetime, reason: str) -> None:
        """Publish a phone lock the app applies on its next wake.

        State, not a command. The server cannot reach into the phone; it can
        only say what it wants to be true, and the app reconciles when it next
        runs. That means a lock is best-effort and may land late — see the
        reconcile in ArgonStore for what iOS actually guarantees.
        """
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('lock', ?)",
                (json.dumps({"until": until.isoformat(), "reason": reason}),))
            self._db.commit()
        self._t.append("lock_set", summary=f"until {until:%H:%M} — {reason}"[:200])

    def lock(self) -> tuple[datetime, str] | None:
        """The live lock, or None once it has lapsed. Expiry is read-time, so a
        lock ends on its own even if nothing runs to clear it."""
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM settings WHERE key='lock'").fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row["value"])
            until = datetime.fromisoformat(data["until"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
        return (until, data.get("reason", "")) if until > clock.now() else None

    def clear_lock(self, why: str = "cleared") -> None:
        with self._lock:
            self._db.execute("DELETE FROM settings WHERE key='lock'")
            self._db.commit()
        self._t.append("lock_cleared", summary=why[:200])

    def clear_quiet(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM settings WHERE key='quiet'")
            self._db.commit()
        self._t.append("stand_down_cleared")


def _due_phrase(due: str) -> str:
    """``Wed 09-16 (today)`` / ``Tue 08-25 (21 days overdue)``."""
    try:
        day = datetime.strptime(due[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return due
    delta = (day - clock.today()).days
    if delta == 0:
        when = "today"
    elif delta == 1:
        when = "tomorrow"
    elif delta < 0:
        when = f"{-delta} day{'s' if delta < -1 else ''} overdue"
    else:
        when = f"in {delta} days"
    return f"{day:%a} {day:%m-%d} ({when})"


def _task(row: sqlite3.Row) -> Task:
    return Task(id=row["id"], title=row["title"], subject=row["subject"],
                due=row["due"], priority=row["priority"], done=bool(row["done"]),
                source=row["source"], external_id=row["external_id"],
                started_at=row["started_at"])


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
        assert len(s.find_tasks("chem")) == 1
        assert s.find_task(b.id).title == "Read Ch 3"
        assert s.find_task("nonsense") is None

        # Two matches must refuse rather than guess.
        s.add_task("Math homework one")
        s.add_task("Math homework two")
        assert len(s.find_tasks("math homework")) == 2
        assert s.find_task("math homework") is None, "ambiguity must not resolve"

        # Dates carry their weekday and their lateness.
        clock.set_for_test(datetime(2026, 9, 16, 9, 0, tzinfo=clock.TZ))
        assert "(today)" in _due_phrase("2026-09-16")
        assert "(tomorrow)" in _due_phrase("2026-09-17")
        assert "21 days overdue" in _due_phrase("2026-08-26")
        assert _due_phrase("2026-09-16").startswith("Wed ")
        assert _due_phrase("nonsense") == "nonsense"
        clock.set_for_test(datetime(2026, 9, 14, 18, 0, tzinfo=clock.TZ))

        # Importing the same external item twice updates rather than duplicates.
        first = s.add_task("HW 17", source="classroom", external_id="cw-17",
                           due="2026-09-12")
        again = s.add_task("HW 17", source="classroom", external_id="cw-17",
                           due="2026-09-13")
        assert again.id == first.id, "same assignment must not appear twice"
        assert s.task(first.id).due == "2026-09-13", "and its due date updates"
        assert list(s.external_ids(source="classroom")) == ["cw-17"]

        # A closure the *sync* made comes back when the source still owes it.
        s.complete_task(first.id, by="sync")
        assert s.task(first.id).done is True
        again2 = s.add_task("HW 17", source="classroom", external_id="cw-17",
                            due="2026-09-13")
        assert again2.done is False, "re-import must reopen a wrongly closed task"

        # A closure *he* made is final. Classroom keeps listing work he has
        # done but not submitted, so an unconditional reopen walked back every
        # checkmark he made — twenty in one evening, each a PATCH answered 200.
        s.complete_task(first.id, by="him")
        again3 = s.add_task("HW 17", source="classroom", external_id="cw-17",
                            due="2026-09-13")
        assert again3.done is True, "the sync must not undo his own checkmark"
        assert "task_reopened" in [e.kind for e in t.window(2)]

        assert s.complete_task(a.id).done is True
        assert s.complete_task(a.id) is None, "completing twice is not an event"
        assert a.id not in [x.id for x in s.tasks()]
        assert s.update_task(b.id, priority="high").priority == "high"
        assert s.update_task(b.id, bogus="x") is not None, "a no-op still returns the task"

        # A no-op update must not write an event: the Classroom sync re-imports
        # every assignment every half hour and would otherwise flood the
        # transcript the model reads.
        before = len([e for e in t.window(2) if e.kind == "task_updated"])
        s.update_task(b.id, priority="high")          # already high
        s.update_task(b.id, title="Read Ch 3")        # already that title
        after = len([e for e in t.window(2) if e.kind == "task_updated"])
        assert before == after, f"no-op update wrote {after - before} events"

        assert s.start_task(b.id).started is True
        assert s.start_task(b.id) is None, "starting twice is not an event"
        assert s.tasks()[0].started_at is not None or s.find_task("Read Ch 3").started

        # A start goes stale. Without this the board says "working on it" days
        # later, because nothing ever clears started_at.
        stale = replace(s.task(b.id),
                        started_at=(clock.now() - STARTED_TTL
                                    - timedelta(minutes=1)).isoformat())
        assert stale.started_at is not None
        assert stale.started is False, "a start from yesterday still reads as current"

        s.remember("practice Tuesdays", standing=True)
        s.remember("lab meeting moved", until="2026-09-01")   # expired
        s.remember("essay due soon", until="2026-12-01")
        # Free text sorts above any real date, so it would never expire.
        s.remember("vague deadline", until="tomorrow")
        assert s.facts() == ["practice Tuesdays", "essay due soon", "vague deadline"], s.facts()
        assert "bad_until" in [e.kind for e in t.window(2)]

        # Every mutation is visible in the transcript, so the agent sees it.
        kinds = [e.kind for e in t.window(2)]
        assert kinds.count("task_added") >= 3  # order-independent
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

        # The lock expires at read time, so it ends on its own if nothing runs.
        assert s.lock() is None
        s.set_lock(clock.now() + timedelta(minutes=30), "homework")
        assert s.lock() is not None and s.lock()[1] == "homework"
        clock.set_for_test(clock.now() + timedelta(hours=1))
        assert s.lock() is None, "a lapsed lock must not keep the phone shut"
        clock.set_for_test(None)
        s.set_lock(clock.now() + timedelta(minutes=30), "homework")
        s.clear_lock("he asked out")
        assert s.lock() is None

        clock.set_for_test(None)
        # An older database must survive the upgrade rather than refuse to open.
        legacy = Path(tmp) / "legacy.db"
        import sqlite3 as _sq
        old = _sq.connect(legacy)
        old.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL,"
                    " subject TEXT, due TEXT, priority TEXT NOT NULL DEFAULT 'normal',"
                    " done INTEGER NOT NULL DEFAULT 0, done_at TEXT, started_at TEXT,"
                    " source TEXT NOT NULL DEFAULT 'him', created_at TEXT NOT NULL)")
        old.execute("INSERT INTO tasks (id,title,created_at) VALUES ('old','Older task','x')")
        old.commit(); old.close()

        migrated = Store(legacy, t)
        assert [x.title for x in migrated.tasks()] == ["Older task"]
        assert migrated.add_task("New", source="classroom", external_id="cw-1").external_id == "cw-1"

    print("store selftest ok")


if __name__ == "__main__":
    _selftest()
