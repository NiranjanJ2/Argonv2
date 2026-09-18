"""Google Calendar, Tasks, Classroom and Gmail.

**Tools register unconditionally, even when an account's grant is dead.**  The
previous system skipped them silently, which hid a three-month outage: the model
simply stopped having calendar tools and never said why.  A stale account now
answers with a sentence naming the account and the fix, which is a real answer
and the only honest one available.

The refresh tokens expire every seven days while the OAuth consent screen sits
in *Testing*.  That is Google policy and nothing here can work around it; the
fix is one Console setting (Audience → Publishing status → Publish app).  If
every account fails with ``invalid_grant`` at once, that is what happened.

ponytail: ``static_discovery=True`` and a cached service per (account, api).
``discovery.build`` otherwise makes an HTTP round trip on every single tool
call, which was a known cost in the old system and never fixed.
"""

from __future__ import annotations

import json
import hashlib
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from argon import clock, config

#: What each capability needs. Accounts are role-specialised — his `work`
#: account holds calendar and tasks, `school` holds Classroom, `personal` holds
#: almost nothing — so a single global scope list is wrong and asking the wrong
#: account returns "insufficient authentication scopes", which reads like a
#: broken grant rather than a misrouted call.
#: The scope each capability needs for the call it actually makes — not the
#: scope that merely sounds related.
#:
#: `classroom` is keyed on `student-submissions.me.readonly`, which is what
#: actually lets `courses().courseWork().list()` read the work for courses he is
#: a student in. Two wrong guesses preceded it: `courses.readonly` alone, which
#: he holds and which 403s on coursework; then `coursework.me.readonly`, which
#: is genuinely sufficient but which he does not hold — and requiring it
#: declared a working grant broken. v1 read assignments with exactly the scopes
#: below, which is the evidence that settles it.
CAPABILITIES: dict[str, str] = {
    "calendar": "https://www.googleapis.com/auth/calendar",
    "tasks": "https://www.googleapis.com/auth/tasks",
    "classroom": "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
    "drive": "https://www.googleapis.com/auth/drive.readonly",
}

#: Alternates that also satisfy a capability.
EQUIVALENT: dict[str, list[str]] = {
    "classroom": [
        "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
        "https://www.googleapis.com/auth/classroom.coursework.me",
    ],
}

#: Requested when authorising a new account, per account, because they are
#: role-specialised. Mirrors what v1 asked for — these grants demonstrably work.
ACCOUNT_SCOPES: dict[str, list[str]] = {
    "personal": ["https://www.googleapis.com/auth/drive.readonly"],
    "work": [
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/tasks",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/gmail.readonly",
    ],
    "school": [
        "https://www.googleapis.com/auth/classroom.courses.readonly",
        "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
        "https://www.googleapis.com/auth/classroom.announcements.readonly",
        # Some teachers post the work as a Material rather than an assignment.
        # Without this scope those classes look like they have no homework.
        "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
        "https://www.googleapis.com/auth/classroom.rosters.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/gmail.readonly",
    ],
}

#: Requested when authorising a *new* account. Existing grants are never
#: widened to match this — they are used for whatever they already cover.
SCOPES = [
    *CAPABILITIES.values(),
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
]


class GoogleUnavailable(RuntimeError):
    """Raised when an account cannot be used.  Rendered to the model as text."""


def token_path(account: str):
    """Where an account's token lives.

    Accepts v1's ``google/<account>/token.json`` layout as well as the flat one,
    so grants that already work keep working instead of being re-authorised.
    """
    nested = config.path("google", account, "token.json")
    if nested.exists():
        return nested
    return config.path("google", f"{account}.json")


def client_secret_path():
    """The OAuth client secret, under any of the names Google hands out.

    The Console downloads it as ``client_secret_<id>.apps.googleusercontent.com
    .json``; people rename it to ``client_secret.json`` or ``client_secrets.json``.
    Globbing costs two lines and saves an hour of "why is it not connecting".
    """
    folder = config.path("google", "_").parent
    for name in ("client_secret.json", "client_secrets.json"):
        if (folder / name).exists():
            return folder / name
    found = sorted(folder.glob("client_secret*.json"))
    return found[0] if found else folder / "client_secret.json"


def granted(account: str) -> set[str]:
    """Scopes an account's token actually holds. Empty when it has none."""
    p = token_path(account)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text()).get("scopes") or [])
    except (json.JSONDecodeError, OSError):
        return set()


def can(account: str, capability: str) -> bool:
    held = granted(account)
    wanted = [CAPABILITIES.get(capability, "")] + EQUIVALENT.get(capability, [])
    return any(scope and scope in held for scope in wanted)


def missing_scope(capability: str) -> str:
    """The short name of the scope an account would need. For error messages."""
    return CAPABILITIES.get(capability, capability).rsplit("/", 1)[-1]


def account_for(capability: str, accounts: list[str]) -> str | None:
    """The first configured account whose grant covers *capability*."""
    return next((a for a in accounts if can(a, capability)), None)


def capabilities(accounts: list[str]) -> dict[str, str | None]:
    """Which account serves what. The map doctor prints."""
    return {name: account_for(name, accounts) for name in CAPABILITIES}


def _credentials(account: str):
    # File check before the import: "not connected" is the commonest failure and
    # should explain itself even on a box where the client libraries are absent.
    p = token_path(account)
    if not p.exists():
        raise GoogleUnavailable(
            f"Google account '{account}' is not connected. "
            f"Run `argon google-auth {account}` on the server."
        )

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    # The token's own scopes, not SCOPES: asking for more than was granted
    # makes the refresh fail with invalid_scope and loses a working grant.
    creds = Credentials.from_authorized_user_file(str(p), sorted(granted(account)) or None)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:  # noqa: BLE001 - the message is the useful part
            raise GoogleUnavailable(
                f"Google account '{account}' needs reauthorising ({e}). "
                f"If every account failed at once, the OAuth consent screen is "
                f"back in Testing — publish it, then `argon google-auth {account}`."
            ) from e
        p.write_text(creds.to_json())
        return creds
    raise GoogleUnavailable(f"Google account '{account}' has no usable credentials.")


@lru_cache(maxsize=32)
def _service(account: str, api: str, version: str):
    # Credentials first, import second. Reversed, a box without the client
    # libraries reports "No module named 'googleapiclient'" for an account that
    # was simply never connected — which sends you installing packages that are
    # already fine. _credentials raises the message that actually helps.
    creds = _credentials(account)

    from googleapiclient.discovery import build

    return build(api, version, credentials=creds,
                 cache_discovery=False, static_discovery=True)


def authorise(account: str, port: int = 8765) -> str:
    """Run the loopback OAuth flow.  Server is headless, so this needs
    `ssh -L 8765:localhost:8765 agentneon` from the Mac."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_secret_path().exists():
        raise GoogleUnavailable(f"put the OAuth client secret at {client_secret_path()}")
    wanted = ACCOUNT_SCOPES.get(account, SCOPES)
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path()), wanted)
    creds = flow.run_local_server(port=port, open_browser=False)
    token_path(account).write_text(creds.to_json())
    _service.cache_clear()
    return f"connected {account}"


#: A cheap real call per capability, to prove the grant rather than trust the file.
_PROBES = {
    "calendar": lambda s: s("calendar", "v3").calendarList().list(maxResults=1).execute(),
    "tasks": lambda s: s("tasks", "v1").tasklists().list(maxResults=1).execute(),
    "classroom": lambda s: s("classroom", "v1").courses().list(pageSize=1).execute(),
    "gmail": lambda s: s("gmail", "v1").users().getProfile(userId="me").execute(),
    "drive": lambda s: s("drive", "v3").files().list(pageSize=1).execute(),
}


def status(accounts: list[str]) -> str:
    """Exercise each grant for real, against something it claims to cover.

    Probing calendar on an account that was never granted calendar reports
    "insufficient scopes", which looks like a dead grant and is really a
    misrouted probe. Each account is tested on what it actually holds.
    """
    if not accounts:
        return "no Google accounts configured"
    lines = []
    for name in accounts:
        have = [c for c in CAPABILITIES if can(name, c)]
        if not have:
            lines.append(f"{name}: no usable scopes"
                         + ("" if token_path(name).exists() else " (not connected)"))
            continue
        results = []
        for capability in have:
            try:
                _PROBES[capability](lambda api, v: _service(name, api, v))
                results.append(capability)
            except GoogleUnavailable as e:
                results.append(f"{capability}: {e}")
            except Exception as e:  # noqa: BLE001
                results.append(f"{capability}: FAILED ({type(e).__name__})")
        lines.append(f"{name}: {', '.join(results)}")
    return "\n".join(lines)


# -- formatting -------------------------------------------------------------

def _when(event: dict[str, Any]) -> str:
    start = event.get("start") or {}
    if "date" in start:
        return f"{start['date']} (all day)"
    raw = start.get("dateTime")
    if not raw:
        return "?"
    return datetime.fromisoformat(raw).astimezone(clock.TZ).strftime("%a %d %b %H:%M")


def format_events(events: list[dict[str, Any]]) -> str:
    """Render events, marking the ones that have already happened.

    A flat list gets relayed flat: Argon listed '14:30 Wittmann Mentoring' as
    upcoming in a 16:03 message, 93 minutes after it ended.
    """
    if not events:
        return "Nothing on the calendar."
    now = clock.now()
    lines = []
    for e in events:
        raw = (e.get("start") or {}).get("dateTime")
        past = ""
        if raw:
            try:
                if datetime.fromisoformat(raw).astimezone(clock.TZ) < now:
                    past = " (already started)"
            except ValueError:
                pass
        lines.append(f"- {_when(e)} — {e.get('summary') or '(untitled)'}{past}")
    return "\n".join(lines)


def format_assignments(items: list[dict[str, Any]], courses: dict[str, str]) -> str:
    if not items:
        return "No assignments outstanding."
    out = []
    for w in items:
        due = w.get("dueDate") or {}
        when = (f"{due['year']}-{due['month']:02d}-{due['day']:02d}"
                if due.get("year") else "no due date")
        course = courses.get(w.get("courseId", ""), "?")
        out.append(f"- {w.get('title') or '(untitled)'} ({course}) due {when}")
    return "\n".join(out)


# -- operations -------------------------------------------------------------

def list_events(account: str, days: int = 7, limit: int = 20) -> str:
    now = clock.now()
    res = _service(account, "calendar", "v3").events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=(now + timedelta(days=days)).isoformat(),
        singleEvents=True, orderBy="startTime", maxResults=limit,
    ).execute()
    return format_events(res.get("items", []))


def todays_events(account: str) -> list[dict[str, Any]]:
    """Today's remaining timed events, soonest first.

    Returned as records so the runtime can state them in the prompt rather
    than hoping the model calls the tool. All-day entries are skipped: they
    are not moments he has to be anywhere for.
    """
    now = clock.now()
    end_of_day = now.replace(hour=23, minute=59, second=59)
    res = _service(account, "calendar", "v3").events().list(
        calendarId="primary", timeMin=now.isoformat(), timeMax=end_of_day.isoformat(),
        singleEvents=True, orderBy="startTime", maxResults=20,
    ).execute()
    out = []
    for e in res.get("items", []):
        raw = (e.get("start") or {}).get("dateTime")
        if not raw:
            continue
        starts = datetime.fromisoformat(raw).astimezone(clock.TZ)
        out.append({"title": e.get("summary") or "(untitled)", "at": starts,
                    "minutes": int((starts - now).total_seconds() // 60)})
    return out


def create_event(account: str, title: str, start: str, end: str = "") -> str:
    begin = datetime.fromisoformat(start)
    if begin.tzinfo is None:
        begin = begin.replace(tzinfo=clock.TZ)
    finish = datetime.fromisoformat(end) if end else begin + timedelta(hours=1)
    if finish.tzinfo is None:
        finish = finish.replace(tzinfo=clock.TZ)
    ev = _service(account, "calendar", "v3").events().insert(
        calendarId="primary",
        body={"summary": title,
              "start": {"dateTime": begin.isoformat()},
              "end": {"dateTime": finish.isoformat()}},
    ).execute()
    return f"added '{title}' at {_when(ev)}"


#: Submission states meaning he still owes the work.
OUTSTANDING = ["CREATED", "RECLAIMED_BY_STUDENT"]

#: The board is a window around now, not everything ever assigned.
#:
#: Taken from v1, which got this right. The window used to open at *now*, so an
#: assignment stopped existing the moment it came due — Chapter 5 Key Terms was
#: on the board all of 09/09 and gone by 00:01 on 09/10, and Argon ended up
#: telling him it had never been posted. Overdue work is the most important
#: thing a board can show, so it reaches backwards.
DAYS_BACK = 14
DAYS_AHEAD = 30

#: Undated coursework is excluded, also from v1.
#:
#: Teachers post handouts, reminders and readings as assignments. There is
#: nothing to turn in, so Classroom reports them outstanding forever — fifty
#: nine items, most of which he was never going to "complete". Anything with a
#: real deadline is real work; anything without one is a notice board.
REQUIRE_DUE_DATE = True


def _all_coursework(svc, course_id: str) -> list[dict[str, Any]]:
    """Every published item in a course, paged.

    Paged because a single page silently truncates a busy course, and a class
    missing from the board is the failure that actually costs him.
    """
    items: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"courseId": course_id,
                                  "courseWorkStates": ["PUBLISHED"], "pageSize": 50}
        if token:
            kwargs["pageToken"] = token
        page = svc.courses().courseWork().list(**kwargs).execute()
        items.extend(page.get("courseWork", []))
        token = page.get("nextPageToken")
        if not token:
            return items


def outstanding_assignments(account: str, days_back: int = DAYS_BACK,
                            days_ahead: int = DAYS_AHEAD
                            ) -> tuple[list[dict[str, Any]], list[str]]:
    """Work he still owes, inside the window, as records rather than prose.

    ``list_assignments`` renders these for the model and the task board takes
    the fields, so both call this and cannot disagree. Returns (assignments,
    courses that could not be read).
    """
    svc = _service(account, "classroom", "v1")
    courses = {c["id"]: c.get("name", "?")
               for c in svc.courses().list(courseStates=["ACTIVE"]).execute().get("courses", [])}
    floor = (clock.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    ceiling = (clock.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    out: list[dict[str, Any]] = []
    refused: list[str] = []
    for cid, name in courses.items():
        try:
            owed = {
                s["courseWorkId"]
                for s in svc.courses().courseWork().studentSubmissions().list(
                    courseId=cid, courseWorkId="-", userId="me",
                    states=OUTSTANDING).execute().get("studentSubmissions", [])
            }
            if not owed:
                continue
            work = _all_coursework(svc, cid)
        except Exception as e:  # noqa: BLE001
            # One locked-down course must not blank out the others.
            refused.append(f"{tidy_course(name)} ({type(e).__name__})")
            continue

        for cw in work:
            if cw.get("id") not in owed:
                continue
            _, due = _due_key(cw)
            if REQUIRE_DUE_DATE and not due:
                continue
            if not (floor <= due <= ceiling):
                continue
            out.append({"id": cw["id"], "title": cw.get("title") or "(untitled)",
                        "course": tidy_course(name), "due": due, "courseId": cid})
    out.sort(key=lambda a: a["due"])
    return out, refused


#: How far back to read posted material. A teacher posts the day's work in the
#: morning; by the third day it is no longer "what to do tonight".
MATERIAL_DAYS_BACK = 3


def recent_materials(account: str, days_back: int = MATERIAL_DAYS_BACK,
                     limit: int = 12) -> list[dict[str, Any]]:
    """Recently posted material and announcements, as context rather than tasks.

    Not every class assigns work as an *assignment*. AP Lang posts the day's
    reading as a ``courseWorkMaterial`` and some teachers just write an
    announcement. Neither has a studentSubmission, so ``outstanding_assignments``
    skips the course entirely and the class reads as having no homework — which
    is the single worst thing this board can say, because he believes it.

    These come back as context lines, never as tasks, and the distinction is
    forced by the data: a material has no due date and no submission state, so
    there is nothing that can ever mark it done. Made into tasks they would
    pile up unfinishable forever. Stated as context the model can see "AP Lang
    posted X today" and ask him about it, or call add_task if it is real work.
    """
    svc = _service(account, "classroom", "v1")
    courses = {c["id"]: c.get("name", "?")
               for c in svc.courses().list(
                   courseStates=["ACTIVE"]).execute().get("courses", [])}
    floor = clock.now() - timedelta(days=days_back)

    out: list[dict[str, Any]] = []
    for cid, name in courses.items():
        for kind, call, state_arg, key, titler in (
            ("material", svc.courses().courseWorkMaterials(),
             "courseWorkMaterialStates", "courseWorkMaterial",
             lambda i: i.get("title") or "(untitled)"),
            ("announcement", svc.courses().announcements(),
             "announcementStates", "announcements",
             lambda i: " ".join((i.get("text") or "").split())[:120] or "(empty)"),
        ):
            try:
                page = call.list(**{"courseId": cid, state_arg: ["PUBLISHED"],
                                    "pageSize": 20}).execute()
            except Exception:  # noqa: BLE001 - a locked course must not stop the rest
                continue
            for item in page.get(key, []):
                at = _parse_rfc3339(item.get("updateTime") or item.get("creationTime"))
                if at is None or at < floor:
                    continue
                out.append({"course": tidy_course(name), "kind": kind,
                            "title": titler(item), "at": at})
    # Materials first, then recency. A material is a teacher posting the day's
    # actual work; an announcement is usually a club or the counselling office.
    # Sorted by recency alone, one busy day of "PSAT registration!" pushes AP
    # Lang's "WEEK 6 - TUES" out of the cap — losing the only reason this exists.
    out.sort(key=lambda m: (m["kind"] != "material", -m["at"].timestamp()))
    return out[:limit]


def _parse_rfc3339(value: str | None) -> datetime | None:
    """Classroom timestamps, in local time. Returns None rather than raising:
    a malformed timestamp on one post must not blank the whole feed."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


#: The homework block inside a daily material post. AP Lang writes the day's
#: agenda, then "HW:", then the work — so everything before it is classwork
#: that is already done by the time he reads this.
_HW_HEADER = re.compile(r"^\s*(?:HW|HOMEWORK)\s*:?\s*$", re.I | re.M)

#: "by Fri., 9/18", "due Tues., 9/15", "due 9/18", "by 9/18".
_HW_DUE = re.compile(r"\b(?:by|due)\b[^0-9]{0,12}(\d{1,2})\s*/\s*(\d{1,2})", re.I)

#: "1. Read The Crucible Act 3" — the numbering AP Lang uses for each item.
_HW_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*(.+?)\s*$", re.M)


def _hw_due_date(text: str, posted: datetime) -> str | None:
    """The "by Fri., 9/18" inside one homework line, as YYYY-MM-DD.

    The year is not written, so it comes from when the post went up: a 1/8 due
    date on a December post is next January, not eleven months ago.
    """
    m = _HW_DUE.search(text)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    year = posted.year + (1 if month < posted.month - 6 else 0)
    try:
        return datetime(year, month, day, tzinfo=clock.TZ).strftime("%Y-%m-%d")
    except ValueError:
        return None


def material_homework(account: str, days_back: int = 10) -> list[dict[str, Any]]:
    """Homework scraped out of daily material posts.

    Some classes never create assignments. AP Lang posts one material a day —
    "WEEK 6 - WED 9/16" — whose description carries the agenda and then an
    "HW:" block. None of it is courseWork, so it has no submission and no due
    date, `outstanding_assignments` never sees it, and the class reads as
    having no homework at all. That is the worst thing this board can say,
    because he believes it.

    Only the HW block is taken. The lines above it are what happened in class
    that day, already done by the time he reads this, and adding them would
    bury the two lines that are actually work.

    Deduplicated on the text itself, because the same item is repeated in every
    post until it is due: "Read The Crucible Act 3 by Fri., 9/18" appears in
    Monday's, Tuesday's, Wednesday's and Thursday's posts and is one task.
    """
    svc = _service(account, "classroom", "v1")
    courses = {c["id"]: c.get("name", "?")
               for c in svc.courses().list(
                   courseStates=["ACTIVE"]).execute().get("courses", [])}
    floor = clock.now() - timedelta(days=days_back)

    seen: dict[str, dict[str, Any]] = {}
    for cid, name in courses.items():
        try:
            page = svc.courses().courseWorkMaterials().list(
                courseId=cid, courseWorkMaterialStates=["PUBLISHED"],
                pageSize=30).execute()
        except Exception:  # noqa: BLE001 - a locked course must not stop the rest
            continue
        for item in page.get("courseWorkMaterial", []):
            posted = _parse_rfc3339(item.get("updateTime")
                                    or item.get("creationTime"))
            if posted is None or posted < floor:
                continue
            body = item.get("description") or ""
            split = _HW_HEADER.split(body, maxsplit=1)
            if len(split) < 2:
                continue
            for line in _HW_ITEM.findall(split[-1]):
                title = " ".join(line.split())
                if len(title) < 4:
                    continue
                key = f"{cid}:{title.lower()}"
                if key in seen:
                    continue
                seen[key] = {
                    # sha1, not hash(): Python randomises string hashing per
                    # process, so every restart minted new ids, the sync saw
                    # them as new work, added them and closed yesterday's — the
                    # same three AP Lang tasks added and completed on a loop.
                    "id": "m" + hashlib.sha1(key.encode()).hexdigest()[:11],
                    "title": _HW_DUE.sub("", title).strip(" .,;–—-") or title,
                    "course": tidy_course(name),
                    "due": _hw_due_date(title, posted),
                    "courseId": cid,
                }
    return [v for v in seen.values() if v["due"]]


def list_assignments(account: str, limit: int = 30, days_back: int = DAYS_BACK) -> str:
    """What he still owes, across his active courses.

    Two things this asks for that the obvious version does not:

    ``courseWorkStates=["PUBLISHED"]`` — without it the API also tries to
    return DRAFT work, which a *student* may not see, and the whole call comes
    back 403 "The caller does not have permission", which reads exactly like a
    missing scope and is not one.

    ``studentSubmissions`` with ``courseWorkId="-"`` — one call per course
    returning every submission, filtered to the states that mean "not handed
    in". Without this the list is everything ever assigned, which is worse than
    no list: he stops reading it.
    """
    items, refused = outstanding_assignments(account, days_back)
    if not items:
        text = "No assignments outstanding."
    else:
        text = "\n".join(
            f"- {a['title']} ({a['course']}) due {a['due'] or 'no due date'}"
            for a in items[:limit])
    if refused:
        text += "\n(could not read: " + ", ".join(refused) + ")"
    return text


#: Year suffixes teachers put on course names. "Math Analysis/Calc A
#: H-Machado(26-27)" wraps to two lines on a phone and the year tells him
#: nothing he does not know.
_YEAR_SUFFIX = re.compile(
    r"\s*[\(\[]?\s*'?\d{2}(?:\d{2})?\s*[-/–]\s*'?\d{2}(?:\d{2})?\s*[\)\]]?\s*$")

#: Teachers append their own name: "Artif Intell H-Johnson", "Math Analysis/
#: Calc A H-Machado". He knows who teaches his classes; on a phone row it just
#: pushes the subject out of view.
_TEACHER_SUFFIX = re.compile(r"\s+[A-Z]-[A-Z][a-z]+\s*$")


def tidy_course(name: str) -> str:
    """Strip the year off a Classroom course name."""
    cleaned = _YEAR_SUFFIX.sub("", name or "").strip(" -–—")
    cleaned = _TEACHER_SUFFIX.sub("", cleaned).strip(" -–—")
    return cleaned or name


def classroom_due(coursework: dict[str, Any]) -> datetime | None:
    """Local due datetime of a courseWork item, or None if it has no deadline.

    The date comes from ``dueDate`` verbatim; only the *time of day* comes from
    ``dueTime`` converted out of UTC. The two fields are not one instant, and
    treating them as one is the bug that put Math a day early on his board.

    Google documents both as UTC, and ``dueTime`` genuinely is — 06:59Z is a
    teacher setting 11:59 PM, not a 6:59 AM deadline. But ``dueDate`` is stored
    as the day the teacher picked, unconverted, and that is the day Classroom
    shows him. So HW 21 arrives as ``dueDate=2026-09-18, dueTime=06:59Z`` and
    Classroom displays "Due Sep 18, 11:59 PM".

    Reading the pair as a single UTC instant gives Sep 17 23:59 — one day
    earlier than both the teacher and the app he checks. Verified against his
    own Classroom: it says Sep 18. Taking the date from one field and the clock
    from the other reconstructs exactly what he sees.

    With no ``dueTime`` there is no official instant; local end-of-day is only
    a work-by fallback.
    """
    due_date = coursework.get("dueDate")
    if not due_date:
        return None
    due_time = coursework.get("dueTime")
    try:
        if due_time is None:
            return datetime(due_date["year"], due_date["month"], due_date["day"],
                            23, 59, tzinfo=clock.TZ)
        # Convert the clock, keep the calendar. The offset is taken on the
        # assignment's own date, not a fixed one: a January reference put every
        # September deadline an hour out, because Pacific is PST then and PDT
        # now. Only the resulting hour and minute are used.
        wall = datetime(due_date["year"], due_date["month"], due_date["day"],
                        due_time.get("hours", 0), due_time.get("minutes", 0),
                        tzinfo=UTC).astimezone(clock.TZ)
        return datetime(due_date["year"], due_date["month"], due_date["day"],
                        wall.hour, wall.minute, tzinfo=clock.TZ)
    except (KeyError, TypeError, ValueError):
        return None


def _due_key(work: dict[str, Any]) -> tuple:
    """Sort by local due date, undated last — the order he reads them in."""
    due = classroom_due(work)
    return (1, "") if due is None else (0, due.strftime("%Y-%m-%d"))


def search_all_mail(accounts: list[str], query: str, limit: int = 5) -> str:
    """Search every account that holds the Gmail scope.

    `account_for` returns the first match, which is `work` — so his **school**
    mailbox, where teachers and counsellors write, was unreachable, and
    "did Mr Johnson email about the lab?" answered "No mail matching" from the
    wrong inbox and read as a real answer.
    """
    usable = [a for a in accounts if can(a, "gmail")]
    if not usable:
        return "No Google account is authorised for mail."
    blocks = []
    for name in usable:
        try:
            found = search_mail(name, query, limit)
        except GoogleUnavailable as e:
            blocks.append(f"[{name}] {e}")
        except Exception as e:  # noqa: BLE001
            blocks.append(f"[{name}] failed: {type(e).__name__}")
        else:
            blocks.append(f"[{name}]\n{found}")
    return "\n\n".join(blocks)


def search_mail(account: str, query: str, limit: int = 5) -> str:
    svc = _service(account, "gmail", "v1")
    ids = svc.users().messages().list(userId="me", q=query, maxResults=limit
                                      ).execute().get("messages", [])
    if not ids:
        return f"No mail matching {query!r}."
    out = []
    for m in ids:
        msg = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]).execute()
        h = {x["name"]: x["value"] for x in msg["payload"].get("headers", [])}
        out.append(f"- {h.get('Subject', '(no subject)')} — {h.get('From', '?')}")
    return "\n".join(out)


def _selftest() -> None:
    """Formatting and the failure path only; the rest needs real credentials."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ARGON_HOME"] = tmp

        assert format_events([]) == "Nothing on the calendar."
        line = format_events([{"summary": "Lab", "start": {"dateTime": "2026-09-14T15:00:00-07:00"}}])
        assert "Lab" in line and "15:00" in line, line
        assert "(all day)" in format_events([{"summary": "Break", "start": {"date": "2026-09-14"}}])

        # An event that has already started must say so, not read as upcoming.
        clock.set_for_test(datetime(2026, 9, 14, 16, 3, tzinfo=clock.TZ))
        past = format_events([{"summary": "Wittmann Mentoring",
                               "start": {"dateTime": "2026-09-14T14:30:00-07:00"}}])
        assert "already started" in past, past
        soon = format_events([{"summary": "Project Sync",
                               "start": {"dateTime": "2026-09-14T19:00:00-07:00"}}])
        assert "already started" not in soon
        clock.set_for_test(None)
        assert "(untitled)" in format_events([{"start": {"date": "2026-09-14"}}])
        assert format_events([{"summary": "x", "start": {}}]).endswith("? — x")

        assert format_assignments([], {}) == "No assignments outstanding."
        got = format_assignments(
            [{"title": "Pset", "courseId": "c1",
              "dueDate": {"year": 2026, "month": 9, "day": 16}}], {"c1": "AP Chem"})
        assert got == "- Pset (AP Chem) due 2026-09-16", got
        assert "no due date" in format_assignments([{"title": "T", "courseId": "c1"}], {"c1": "C"})

        # Dated work sorts before undated, and by date.
        rows = [{"title": "late", "dueDate": {"year": 2026, "month": 9, "day": 1}},
                {"title": "none"},
                {"title": "soon", "dueDate": {"year": 2026, "month": 9, "day": 20}}]
        assert [w["title"] for w in sorted(rows, key=_due_key)] == ["late", "soon", "none"]

        # A missing account explains itself rather than disappearing.
        try:
            _credentials("nobody")
            raise AssertionError("should have raised")
        except GoogleUnavailable as e:
            assert "not connected" in str(e) and "google-auth nobody" in str(e)

        # Either filename is found, and the nested v1 layout is honoured.
        folder = config.path("google", "_").parent
        (folder / "client_secrets.json").write_text("{}")
        assert client_secret_path().name == "client_secrets.json"
        (folder / "client_secret.json").write_text("{}")
        assert client_secret_path().name == "client_secret.json"
        nested = config.path("google", "school", "token.json")
        nested.write_text("{}")
        assert token_path("school") == nested
        assert token_path("other").name == "other.json"

        assert status([]) == "no Google accounts configured"
        assert "not connected" in status(["nobody"])

        # Capability routing: accounts are role-specialised, so the right
        # account for calendar is not the right one for Classroom.
        def write_token(name: str, scopes: list[str]) -> None:
            token_path(name).write_text(json.dumps({"scopes": scopes}))

        write_token("personal", ["https://www.googleapis.com/auth/drive.readonly"])
        write_token("work", [CAPABILITIES["calendar"], CAPABILITIES["tasks"],
                             CAPABILITIES["gmail"]])
        write_token("school", [CAPABILITIES["classroom"], CAPABILITIES["gmail"]])
        # His real school token: courses + submissions but NOT coursework.
        write_token("halfschool", [
            "https://www.googleapis.com/auth/classroom.courses.readonly",
            "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly"])
        accounts = ["personal", "work", "school"]

        assert granted("personal") == {CAPABILITIES["drive"]}
        assert can("work", "calendar") and not can("work", "classroom")
        assert can("school", "classroom") and not can("school", "calendar")

        assert account_for("calendar", accounts) == "work"
        assert account_for("classroom", accounts) == "school"
        assert account_for("drive", accounts) == "personal", "first match wins"
        assert account_for("gmail", accounts) == "work", "order decides ties"
        assert account_for("nothing_like_this", accounts) is None
        # courses.readonly alone must not pass for classroom — that mismatch
        # produced an evening of 403s. But his real school grant, which has
        # student-submissions, must pass: v1 read assignments with exactly it.
        write_token("coursesonly", ["https://www.googleapis.com/auth/classroom.courses.readonly"])
        assert not can("coursesonly", "classroom")
        assert can("halfschool", "classroom"), "the grant v1 used must be accepted"
        assert account_for("classroom", ["halfschool"]) == "halfschool"
        assert missing_scope("classroom") == "classroom.student-submissions.me.readonly"

        # Course names lose the year; everything else survives intact.
        assert tidy_course("WHS Robotics Cabinet 26/27") == "WHS Robotics Cabinet"
        assert tidy_course("Kokoro Kara '26-'27") == "Kokoro Kara"
        assert tidy_course("APUSH PM") == "APUSH PM"
        assert tidy_course("Japanese 4 & AP") == "Japanese 4 & AP"
        assert tidy_course("Artif Intell H-Johnson(2026-2027)") == "Artif Intell"
        assert tidy_course("Math Analysis/Calc A H-Machado(26-27)") == "Math Analysis/Calc A"
        assert tidy_course("") == ""

        # The window and the due-date rule are the two things v1 got right
        # about Classroom, and both are easy to lose by accident.
        assert DAYS_BACK >= 7, "overdue work is the point of the board"
        assert REQUIRE_DUE_DATE, "undated handouts are a notice board, not homework"

        # The UTC bug that put every dated assignment a day late. 06:59Z is
        # 23:59 Pacific the previous day, which is the Classroom default.
        late = {"dueDate": {"year": 2026, "month": 9, "day": 22},
                "dueTime": {"hours": 6, "minutes": 59}}
        assert classroom_due(late).strftime("%Y-%m-%d %H:%M") == "2026-09-21 23:59"
        assert _due_key(late) == (0, "2026-09-21")

        # A real daytime deadline survives unshifted.
        noonish = {"dueDate": {"year": 2026, "month": 9, "day": 1},
                   "dueTime": {"hours": 16, "minutes": 30}}
        assert classroom_due(noonish).strftime("%Y-%m-%d %H:%M") == "2026-09-01 09:30"

        # No dueTime means no official instant; end of the stated local day.
        assert classroom_due({"dueDate": {"year": 2026, "month": 9, "day": 5}}
                             ).strftime("%Y-%m-%d %H:%M") == "2026-09-05 23:59"
        assert classroom_due({}) is None
        assert classroom_due({"dueDate": {"year": 2026}}) is None
        # The documented alternate scope still satisfies it.
        write_token("alt", EQUIVALENT["classroom"])
        assert can("alt", "classroom")

        caps = capabilities(accounts)
        assert caps["calendar"] == "work" and caps["classroom"] == "school"

        # An account with no usable scopes says so rather than looking broken.
        write_token("empty", [])
        assert "no usable scopes" in status(["empty"])
        assert granted("missing-entirely") == set()

        del os.environ["ARGON_HOME"]
    print("google selftest ok")


if __name__ == "__main__":
    _selftest()
