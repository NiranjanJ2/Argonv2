"""The afternoon planning sheet, and the evening block that hangs off it.

Ported from v1's ``argon/planner.py``. The problem it solved there is the same
one here: the board assumed anything overdue was still outstanding and had no
way to learn otherwise, so work he had finished on paper stayed on it for days,
got counted in every brief, and was asked about again each evening. Four days
of "SAT reading study is overdue" is how a board stops being believed.

So once a day, after school, the app opens one sheet that *asks* instead:

1. **Long-term** — open work that is neither late nor due today. Asked first,
   before the deadlines are visible, because it loses every time it shares a
   screen with something on fire.
2. **Past due** — each late item gets an answer.
3. **Anything else** — the two things no source can see: AP Chem, assigned in
   class every other day and never on Classroom, and AP Lang, posted as a
   Material whose "HW:" block is the homework.
4. **Your day** — when he means to start, which the phone turns into tonight's
   block (see `routine`).

Deliberately once per day and only after ``OPENS_AFTER``. Twice would be a nag;
before school lets out he does not know the answer to "did Chem assign
anything".

What changed from v1, and why:

- **Classroom owns Classroom dates.** v1 "carried" a late or long-term pick by
  moving its due date to today. v2's sync restores the teacher's date within
  minutes, so a carried assignment silently went back to being late. Picks are
  recorded as today's *focus* instead — a list of ids the app files under
  tonight — and only his own tasks can be moved ("do today").
- **Late Classroom work can be "not doing".** That is the ignore disposition,
  which the sync honours; "done" records the done disposition. Both go through
  the same paths as the tools, so the next import cannot resurrect them.
- **Chem is only asked on a school day.** v1 asked every afternoon including
  Saturday, which made the sheet open on days with nothing to decide.
- **Lang lines already on the board are not offered.** The teacher sometimes
  also posts the work as real coursework, and ticking both put it on the board
  twice under two spellings — the failure google.recent_materials warns about.

State lives in one settings row. Nothing here reaches the network: the caller
passes AP Lang's posts in, so this module self-tests without Google.
"""

from __future__ import annotations

import re
from datetime import datetime, time
from typing import Any

from argon import bell, clock

#: AP Lang posts the day's work as a Material at about 3:36, and school is out.
#: Before this he cannot answer "did Chem assign anything today".
OPENS_AFTER = time(15, 36)

#: What AP Chem costs when it is assigned. He gets it in class every other day
#: and it is never on Classroom, so the only way it reaches the board is here.
CHEM_MINUTES = 60
CHEM_TITLE = "AP Chem homework"
CHEM_SUBJECT = "AP Chemistry"

#: Matched case-insensitively against the tidied course name.
LANG_COURSE = "lang"
LANG_SUBJECT = "AP English Lang"

#: How long before the start he gets told. Long enough to finish what he is
#: doing, short enough that he has not forgotten by the time it lands.
WARNING_MINUTES = 30

#: The block is a holding pattern, not the session: starting a task raises its
#: own lock. This ceiling only matters if he never starts at all — an evening
#: that blocks forever because he went out is the failure worth avoiding.
BLOCK_WINDOW_MIN = 90

#: When the evening starts if he never fills in the sheet. The block at this
#: hour is the thing that makes him fill it in.
DEFAULT_START_HHMM = "18:00"

#: Sun-Thu, the evenings with a school day after them. Python weekday numbers,
#: which is what the phone's ArgonRoutineSettings expects.
SCHOOL_NIGHTS = (6, 0, 1, 2, 3)

#: Per-list cap on a submission. One 1 MB request once created 5,023 tasks and
#: as many transcript rows, every one of which then entered the model's context.
MAX_ITEMS = 100
MAX_TITLE_CHARS = 500

_KEY = "planner"

#: Pulls the homework out of an AP Lang daily post. The teacher's format is
#: stable: an "HW:" line, then numbered items, and "None :)" for a free night.
_HW_BLOCK = re.compile(r"\bHW\s*:?\s*\n?(.+)", re.IGNORECASE | re.DOTALL)
_HW_ITEM = re.compile(r"^\s*\d+[.)]\s*(.+?)\s*$", re.MULTILINE)
#: A number starting an item mid-line, for a post that arrives flattened.
_INLINE_ITEM = re.compile(r"\s+(?=\d+[.)]\s)")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# ---------------------------------------------------------------------------
# State: one settings row, keyed by day so yesterday's answers expire unaided
# ---------------------------------------------------------------------------

def _state(store) -> dict[str, Any]:
    value = store.setting(_KEY, {})
    return value if isinstance(value, dict) else {}


def _save(store, **changes: Any) -> None:
    store.put_setting(_KEY, {**_state(store), **changes})


def last_planned(store) -> str | None:
    return _state(store).get("last_planned")


def mark_planned(store, day: str | None = None) -> str:
    day = day or clock.day_key()
    _save(store, last_planned=day)
    return day


def start_time(store, now: datetime | None = None) -> str | None:
    """Today's chosen start, ``HH:MM``, or None if he has not chosen one."""
    state = _state(store)
    if state.get("start_for") != clock.day_key(now):
        return None
    return state.get("start_at")


def set_start_time(store, hhmm: str | None, now: datetime | None = None) -> None:
    """Record when he means to begin. ``None`` clears it back to the default."""
    _save(store, start_at=hhmm, start_for=clock.day_key(now) if hhmm else None)


def focus(store, now: datetime | None = None) -> list[str]:
    """Task ids he chose to work on today without moving their dates."""
    state = _state(store)
    if state.get("focus_for") != clock.day_key(now):
        return []
    return [str(i) for i in state.get("focus") or []]


def parse_hhmm(value: Any) -> str | None:
    """``"7:05"`` -> ``"07:05"``; anything else -> None."""
    if not isinstance(value, str):
        return None
    m = _HHMM.match(value.strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


# ---------------------------------------------------------------------------
# When it opens, and what it shows
# ---------------------------------------------------------------------------

def is_due(store, now: datetime | None = None) -> bool:
    """Once a day, after school, not before — and not again once answered."""
    now = now or clock.now()
    if now.time() < OPENS_AFTER:
        return False
    return last_planned(store) != clock.day_key(now)


def _school_today(now: datetime) -> bool:
    return bell.schedule_for(now.date()) is not None


def _is_classroom(task) -> bool:
    return task.source == "classroom" and bool(task.external_id)


def _entry(task, today: str) -> dict[str, Any]:
    due = (task.due or "")[:10] or None
    entry = {"id": task.id, "title": task.title, "subject": task.subject or "",
             "due": due, "source": task.source, "classroom": _is_classroom(task)}
    if due and due < today:
        try:
            entry["days_overdue"] = (datetime.strptime(today, "%Y-%m-%d")
                                     - datetime.strptime(due, "%Y-%m-%d")).days
        except ValueError:
            entry["days_overdue"] = None
    return entry


def _words(text: str) -> frozenset[str]:
    return frozenset(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())


def _same_work(a: frozenset[str], b: frozenset[str]) -> bool:
    """Loose title match: one title's words contain the other's.

    Word sets rather than substrings because the teacher's post and her
    coursework spell the same thing in different orders — "Personal Harper's
    Index" against "Harper's Index (personal)". Two words minimum, or "Read"
    would match every reading on the board.
    """
    small, large = sorted((a, b), key=len)
    return len(small) >= 2 and small <= large


def lang_homework(posts: list[dict[str, Any]], today: str | None = None) -> list[str]:
    """Homework lines from today's AP Lang post, or [] if there is none.

    *posts* are ``google.recent_materials`` rows: ``course``, ``kind``, ``at``
    (a datetime), and ``text`` with its line breaks (``title`` as a fallback).

    "None :)" is a real answer and must come back empty rather than as an item
    called "None" — the whole point of reading the post is to know which it is.
    """
    today = today or clock.day_key()
    for post in posts:
        if LANG_COURSE not in str(post.get("course", "")).lower():
            continue
        at = post.get("at")
        day = clock.day_key(at) if isinstance(at, datetime) else str(at or "")[:10]
        if day != today:
            continue
        text = post.get("text") or post.get("title") or ""
        match = _HW_BLOCK.search(text)
        if not match:
            continue
        block = match.group(1)
        if "\n" not in block.strip():
            # Flattened upstream ("HW: 1. Read 2. Annotate"): put the numbered
            # items back on their own lines so the one parser serves both.
            block = _INLINE_ITEM.sub("\n", block)
        items = []
        for line in _HW_ITEM.findall(block):
            cleaned = line.strip(" .")
            if not cleaned or cleaned.lower().startswith("none"):
                continue
            items.append(cleaned)
        return items
    return []


def build(store, lang_posts: list[dict[str, Any]] | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    """Everything the planning sheet needs to render."""
    now = now or clock.now()
    today = clock.day_key(now)

    overdue: list[dict[str, Any]] = []
    due_today: list[dict[str, Any]] = []
    long_term: list[dict[str, Any]] = []
    for task in store.tasks():
        due = (task.due or "")[:10]
        if due and due < today:
            overdue.append(_entry(task, today))
        elif due == today:
            due_today.append(_entry(task, today))
        # Everything else is work he could pull forward. v1 first used a
        # fortnight horizon here, which was an invented rule and wrong: nearly
        # everything sits a day or three out, so the list was always empty. A
        # thing due Friday is the main candidate on a Wednesday.
        #
        # An undated Classroom item is almost never work — teachers post
        # notices as coursework, and those have no date at all.
        elif due or not _is_classroom(task):
            long_term.append(_entry(task, today))

    suggestions: list[dict[str, Any]] = []
    if _school_today(now):
        suggestions.append({
            "kind": "chem",
            "title": CHEM_TITLE,
            "subject": CHEM_SUBJECT,
            "estimate_min": CHEM_MINUTES,
            "prompt": "Did AP Chem assign homework today?",
            # Never pre-ticked. Chem is invisible to every source Argon has, so
            # a default of "yes" would be inventing work and a default of "no"
            # would be asserting a free night. He is the only one who knows.
            "default": False,
        })
    on_board = [_words(t.title) for t in store.tasks()]
    for line in lang_homework(lang_posts or [], today):
        if any(_same_work(_words(line), title) for title in on_board):
            continue
        suggestions.append({
            "kind": "lang",
            "title": line,
            "subject": LANG_SUBJECT,
            "prompt": "From today's AP Lang post",
            "default": True,
        })

    return {
        "needed": is_due(store, now) and bool(overdue or long_term or suggestions),
        "opens_after": OPENS_AFTER.strftime("%H:%M"),
        "last_planned": last_planned(store),
        "today_key": today,
        "overdue": overdue,
        "today": due_today,
        "long_term": long_term,
        "suggestions": suggestions,
        "start_at": start_time(store, now),
        "default_start": DEFAULT_START_HHMM,
        "warning_minutes": WARNING_MINUTES,
    }


def status(store, now: datetime | None = None) -> dict[str, Any]:
    """The cheap part of `build`, for /v2/state on every refresh.

    `due` is exact without reading Classroom: Lang lines only ever appear on a
    school day, and a school day always asks about Chem, so "anything to
    decide" is late work, long-term work, or school today. The app fetches the
    full sheet only when this says so.
    """
    now = now or clock.now()
    today = clock.day_key(now)

    def decidable(task) -> bool:
        """Late or long-term, by the same rules `build` sorts with."""
        if task.due:
            return task.due[:10] != today
        return not _is_classroom(task)

    decide = _school_today(now) or any(decidable(t) for t in store.tasks())
    return {"due": is_due(store, now) and decide,
            "opens_after": OPENS_AFTER.strftime("%H:%M"),
            "planned_for": last_planned(store),
            "focus": focus(store, now)}


def routine(store, now: datetime | None = None) -> dict[str, Any]:
    """What the phone needs to build tonight's schedule for itself.

    The clock lives on the device. A `DeviceActivitySchedule` fires with the
    app closed, the server down and the network off; v1's server cron could
    only publish a desired mode and hope the phone was listening, and for six
    days it was not. So the server does not decide *when*. It says what time he
    chose and lets the phone keep it.
    """
    chosen = start_time(store, now)
    return {
        "start_at": chosen or DEFAULT_START_HHMM,
        "chosen": chosen is not None,
        "default_start": DEFAULT_START_HHMM,
        "planned_today": last_planned(store) == clock.day_key(now),
        "school_nights": list(SCHOOL_NIGHTS),
        "window_minutes": BLOCK_WINDOW_MIN,
        "warning_minutes": WARNING_MINUTES,
    }


# ---------------------------------------------------------------------------
# Applying what he decided
# ---------------------------------------------------------------------------

def _ids(body: dict[str, Any], key: str) -> list[str]:
    value = body.get(key)
    if not isinstance(value, list):
        return []
    return [str(v) for v in value[:MAX_ITEMS] if isinstance(v, (str, int))]


def too_large(body: dict[str, Any]) -> bool:
    return any(isinstance(v, list) and len(v) > MAX_ITEMS for v in body.values())


def apply(rt, body: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Apply the sheet, and record that today has been planned.

    Body, every key optional::

        done    [id]   finished — Classroom work gets the done disposition
        ignore  [id]   Classroom work he is not doing (ignored disposition)
        today   [id]   his own tasks, due date moved to today
        carry   [id]   v1's name; own tasks move, Classroom joins focus
        focus   [id]   work for tonight without touching its date
        add     [{title, subject?, due?}]
        chem    bool   AP Chem assigned today
        start_at "HH:MM" | null

    Completion records the Classroom disposition exactly as a tap on the board
    does, so the next sync cannot resurrect the work; a task he is running goes
    through `rt.end_task` itself so its lock comes down too.
    """
    now = now or clock.now()
    today = clock.day_key(now)
    store = rt.store
    result: dict[str, Any] = {"completed": [], "ignored": [], "moved": [],
                              "focus": [], "added": [], "errors": []}

    def open_task(tid: str, verb: str):
        task = store.task(tid)
        if task is None or task.done:
            result["errors"].append(f"{verb} {tid}: no such open task")
            return None
        return task

    for tid in _ids(body, "done"):
        if not (task := open_task(tid, "done")):
            continue
        if task.started:
            # A running task may hold the shield; end_task is what lifts it.
            done = rt.end_task(tid, done=True)
        else:
            # The same two writes end_task makes, without its per-task push:
            # "all of it is done" on twenty late items was twenty silent pushes
            # inside one request, and the phone asking is already awake.
            if _is_classroom(task):
                store.set_disposition(task.external_id, "done")
            done = store.complete_task(tid, by="him")
        if done:
            result["completed"].append(task.title)

    for tid in _ids(body, "ignore"):
        if not (task := open_task(tid, "ignore")):
            continue
        if not _is_classroom(task):
            result["errors"].append(f"ignore {tid}: not Classroom work")
            continue
        store.set_disposition(task.external_id, "ignored")
        store.complete_task(tid, by="him")
        result["ignored"].append(task.title)

    focus_ids = list(_ids(body, "focus"))
    for tid in _ids(body, "today") + _ids(body, "carry"):
        if not (task := open_task(tid, "today")):
            continue
        if _is_classroom(task):
            # The sync would put the teacher's date back within minutes, so a
            # move here would read as done and then silently undo itself.
            focus_ids.append(tid)
        elif store.update_task(tid, due=today):
            result["moved"].append(task.title)
    # Replaced only when this submission says something about it, so a later
    # "just change my start time" does not wipe the evening he picked.
    if focus_ids or "focus" in body:
        kept = [tid for tid in dict.fromkeys(focus_ids)
                if (t := store.task(tid)) is not None and not t.done]
        _save(store, focus=kept, focus_for=today)
    result["focus"] = [t.title for tid in focus(store, now)
                       if (t := store.task(tid)) is not None]

    additions = [a for a in (body.get("add") or [])[:MAX_ITEMS] if isinstance(a, dict)] \
        if isinstance(body.get("add"), list) else []
    if body.get("chem") is True:
        additions.append({"title": CHEM_TITLE, "subject": CHEM_SUBJECT})
    for item in additions:
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        subject, due = item.get("subject"), item.get("due")
        added = store.add_task(
            title.strip()[:MAX_TITLE_CHARS],
            subject=subject[:120] if isinstance(subject, str) else "",
            due=due[:10] if isinstance(due, str) and due else today,
            source="planner")
        result["added"].append(added.title)

    # Sent as null to clear it. An unparseable time is reported, not guessed:
    # a block at the wrong hour is worse than the default one.
    if "start_at" in body:
        raw = body.get("start_at")
        hhmm = parse_hhmm(raw)
        if raw is not None and hhmm is None:
            result["errors"].append(f"start_at {str(raw)[:20]!r} is not HH:MM")
        else:
            set_start_time(store, hhmm, now)

    # Recorded even when nothing changed: "I looked and there is nothing to
    # move" is an answer, and without it the sheet reopens on the next launch.
    result["planned_for"] = mark_planned(store, today)
    result["routine"] = routine(store, now)
    rt.transcript.append("planned", summary=summarise(result)[:300])
    return result


def summarise(result: dict[str, Any]) -> str:
    """One transcript line, so the agent knows the evening he chose."""
    bits = [f"{len(result[k])} {label}" for k, label in
            (("completed", "done"), ("ignored", "not doing"), ("moved", "moved to today"),
             ("added", "added")) if result.get(k)]
    if result.get("focus"):
        bits.append("working on " + "; ".join(result["focus"])[:120])
    routine_ = result.get("routine") or {}
    if routine_.get("chosen"):
        bits.append(f"starting at {routine_['start_at']}")
    return "He planned the afternoon: " + (", ".join(bits) if bits else "nothing to change")


# ---------------------------------------------------------------------------

def _selftest() -> None:
    import os
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    from argon.store import Store
    from argon.transcript import Transcript

    def at(day: int, hour: int, minute: int = 0) -> datetime:
        # 2026-09-14 is a Monday.
        return datetime(2026, 9, day, hour, minute, tzinfo=clock.TZ)

    # -- the Lang parser, which needs no store -----------------------------
    post = {"course": "AP English Lang", "kind": "material", "at": at(14, 15, 37),
            "text": "WEEK 6 - MON 9/14\n1. Read the thing\nHW:\n"
                    "1. Personal Harper's Index\n2. Read p. 4-7."}
    # Only what follows "HW:" is homework; the classwork above it is not.
    assert lang_homework([post], "2026-09-14") == ["Personal Harper's Index", "Read p. 4-7"]
    # "None :)" is a free night: empty, never a task called "None".
    free = dict(post, text="WEEK 6\nHW:\n1. None :)")
    assert lang_homework([free], "2026-09-14") == []
    assert lang_homework([dict(post, text="WEEK 6 HW: None :)")], "2026-09-14") == []
    # Yesterday's post is not today's homework.
    assert lang_homework([dict(post, at=at(13, 15, 40))], "2026-09-14") == []
    # A post with no HW block, and another course's HW block, yield nothing.
    assert lang_homework([dict(post, text="Reminder: picture day")], "2026-09-14") == []
    assert lang_homework([dict(post, course="APUSH")], "2026-09-14") == []
    # Flattened upstream, the numbered items still come apart.
    flat = dict(post, text="WEEK 6 HW: 1. Annotate essay 2. Bring laptop", title="x")
    assert lang_homework([flat], "2026-09-14") == ["Annotate essay", "Bring laptop"]
    # Falls back to the flattened title when a row has no text.
    assert lang_homework([{"course": "AP Lang", "at": "2026-09-14T15:37",
                           "title": "HW: 1. Read ch 2"}], "2026-09-14") == ["Read ch 2"]

    assert parse_hhmm("7:05") == "07:05" and parse_hhmm("19:30") == "19:30"
    assert parse_hhmm("24:00") is None and parse_hhmm("7pm") is None
    assert parse_hhmm(None) is None and parse_hhmm(1930) is None

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ARGON_HOME"] = tmp       # bell reads overrides.json from here
        clock.set_for_test(at(14, 16))
        t = Transcript(Path(tmp) / "t.db")
        s = Store(Path(tmp) / "s.db", t)

        # -- is_due -----------------------------------------------------------
        assert not is_due(s, at(14, 11)), "not at lunchtime"
        assert not is_due(s, at(14, 15, 35)), "one minute before school is out"
        assert is_due(s, at(14, 15, 36)), "once the Lang post has landed"
        assert is_due(s, at(14, 23, 0))
        mark_planned(s, "2026-09-14")
        assert not is_due(s, at(14, 18)), "only once a day"
        assert is_due(s, at(15, 16)), "a new day asks again"
        s.put_setting(_KEY, {})

        # -- build ------------------------------------------------------------
        late_mine = s.add_task("SAT reading study", due="2026-09-10")
        late_cw = s.add_task("HW 3", subject="Math", due="2026-09-12",
                             source="classroom", external_id="cw-late")
        tonight = s.add_task("Lab report", due="2026-09-14")
        later = s.add_task("Essay draft", due="2026-09-18", source="classroom",
                           external_id="cw-later")
        notice = s.add_task("Club notice", source="classroom", external_id="cw-notice")
        s.add_task("Harper's Index (personal)", due="2026-09-15",
                   source="classroom", external_id="cw-hi")

        view = build(s, [post], at(14, 16))
        assert view["needed"] is True
        assert [i["id"] for i in view["overdue"]] == [late_mine.id, late_cw.id]
        assert view["overdue"][0]["days_overdue"] == 4
        assert view["overdue"][1]["classroom"] is True
        assert [i["id"] for i in view["today"]] == [tonight.id]
        ids = [i["id"] for i in view["long_term"]]
        assert later.id in ids and notice.id not in ids, "undated Classroom is a notice"
        chem = next(x for x in view["suggestions"] if x["kind"] == "chem")
        assert chem["default"] is False, "Chem is never assumed either way"
        lang = [x["title"] for x in view["suggestions"] if x["kind"] == "lang"]
        # Harper's Index is already coursework on the board; offering it again
        # is how the same work landed twice under two spellings.
        assert lang == ["Read p. 4-7"], lang

        # Saturday: no school, so no Chem question.
        assert not any(x["kind"] == "chem" for x in build(s, [], at(19, 16))["suggestions"])

        # Nothing to decide, nothing opens: an empty Saturday board.
        with tempfile.TemporaryDirectory() as tmp2:
            t2 = Transcript(Path(tmp2) / "t.db")
            empty = Store(Path(tmp2) / "s.db", t2)
            assert build(empty, [], at(19, 16))["needed"] is False
            assert status(empty, at(19, 16))["due"] is False
            assert status(empty, at(14, 16))["due"] is True, "a school day asks Chem"
        assert status(s, at(14, 16))["due"] is True

        # -- routine ----------------------------------------------------------
        r = routine(s, at(14, 16))
        assert r == {"start_at": "18:00", "chosen": False, "default_start": "18:00",
                     "planned_today": False, "school_nights": [6, 0, 1, 2, 3],
                     "window_minutes": 90, "warning_minutes": 30}, r

        # -- apply --------------------------------------------------------------
        ended: list[str] = []

        def end_task(tid: str, *, done: bool):
            ended.append(tid)
            task = s.task(tid)
            if task and task.source == "classroom" and task.external_id:
                s.set_disposition(task.external_id, "done")
            return s.complete_task(tid, by="him")

        rt = SimpleNamespace(store=s, transcript=t, end_task=end_task)
        out = apply(rt, {"done": [late_mine.id], "carry": [late_cw.id],
                         "focus": [later.id], "chem": True,
                         "add": [{"title": "Read p. 4-7", "subject": LANG_SUBJECT},
                                 {"title": "  "}, "junk"],
                         "start_at": "19:30"}, at(14, 16))
        assert ended == [], "nothing was running, so no per-task push"
        assert s.task(late_mine.id).done
        # A carried Classroom item is focus, not a date change the sync undoes.
        assert s.task(late_cw.id).due == "2026-09-12"
        assert focus(s, at(14, 17)) == [later.id, late_cw.id]
        assert focus(s, at(15, 17)) == [], "focus is for one day"
        titles = [x.title for x in s.tasks()]
        assert CHEM_TITLE in titles and "Read p. 4-7" in titles
        assert out["errors"] == [], out["errors"]
        assert routine(s, at(14, 17))["start_at"] == "19:30"
        assert routine(s, at(14, 17))["chosen"] and routine(s, at(14, 17))["planned_today"]
        assert routine(s, at(15, 17))["start_at"] == "18:00", "tomorrow is the default"
        assert not is_due(s, at(14, 18)), "submitting closes it for the day"
        assert "starting at 19:30" in [e for e in t.window(2) if e.kind == "planned"][-1] \
            .payload["summary"]

        # A running task goes through the runtime, which lifts its lock.
        running = s.add_task("Worksheet", due="2026-09-12", source="classroom",
                             external_id="cw-run")
        s.start_task(running.id)
        apply(rt, {"done": [running.id]}, at(14, 16))
        assert ended == [running.id] and s.disposition("cw-run") == "done"

        # Not doing: the ignored disposition, so the sync cannot resurrect it.
        out = apply(rt, {"ignore": [late_cw.id, tonight.id], "today": [late_mine.id]},
                    at(14, 16))
        assert s.disposition("cw-late") == "ignored" and s.task(late_cw.id).done
        assert any("not Classroom" in e for e in out["errors"])
        assert any("no such open task" in e for e in out["errors"]), "already done"

        # Own tasks move; bad times are refused, not guessed; null clears.
        mine = s.add_task("Flashcards", due="2026-09-11")
        out = apply(rt, {"today": [mine.id], "start_at": "7pm"}, at(14, 16))
        assert s.task(mine.id).due == "2026-09-14" and out["moved"] == ["Flashcards"]
        assert start_time(s, at(14, 16)) == "19:30" and out["errors"]
        apply(rt, {"start_at": None}, at(14, 16))
        assert start_time(s, at(14, 16)) is None

        # An empty submission still marks the day planned.
        s.put_setting(_KEY, {})
        assert apply(rt, {}, at(14, 16))["planned_for"] == "2026-09-14"
        assert not is_due(s, at(14, 17))
        assert too_large({"done": ["x"] * (MAX_ITEMS + 1)})

        clock.set_for_test(None)
        del os.environ["ARGON_HOME"]
    print("planner selftest ok")


if __name__ == "__main__":
    _selftest()
