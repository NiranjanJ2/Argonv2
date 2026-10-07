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

import base64
import json
import hashlib
import re
import threading
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
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


# One service per thread. A googleapiclient service owns an httplib2.Http, which
# is not thread-safe: two threads sharing its SSL socket interleave records and
# the journal shows DECRYPTION_FAILED_OR_BAD_RECORD_MAC. Thread-local cache keeps
# the "no discovery round trip per call" win without sharing a connection.
#
# Invalidation is the token file's signature, checked on every call: `argon
# google-auth` runs in a separate CLI process and cannot reach this one's memory,
# but it does rewrite the file. If it changes during a build, rebuild on the next
# call too. One extra build after a credential refresh is cheap and avoids caching
# an old service under a newly reauthorised token's signature.
_local = threading.local()


def _token_signature(account: str):
    try:
        st = token_path(account).stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def _service(account: str, api: str, version: str):
    if not hasattr(_local, "cache"):
        _local.cache = {}
    key = (account, api, version)
    entry = _local.cache.get(key)
    signature = _token_signature(account)
    if entry and entry[0] == signature:
        return entry[1]
    svc = _build_service(account, api, version)
    _local.cache[key] = (signature, svc)
    return svc


def _build_service(account: str, api: str, version: str):
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


def coursework_details(account: str, coursework_id: str, course_id: str = "") -> str:
    """One assignment's body: the teacher's instructions and what is attached.

    The board carries titles only. On 09-24 he asked which questions HW 25
    was and Argon searched his mail nine times, because nothing it could call
    returned an assignment's description — which is where Machado writes
    them. Links to attachments are returned as links; a PDF's pages are not.
    """
    svc = _service(account, "classroom", "v1")
    courses = [course_id] if course_id else [
        c["id"] for c in svc.courses().list(courseStates=["ACTIVE"]).execute().get("courses", [])]
    for cid in courses:
        try:
            cw = svc.courses().courseWork().get(courseId=cid, id=coursework_id).execute()
        except Exception:  # noqa: BLE001 — not in this course; try the next
            continue
        return render_coursework(cw)
    return "That assignment is not in any of his active courses."


def render_coursework(cw: dict[str, Any]) -> str:
    lines = [cw.get("title") or "(untitled)"]
    if (body := (cw.get("description") or "").strip()):
        lines.append(body[:1500])
    else:
        lines.append("(no written instructions)")
    for m in cw.get("materials") or []:
        if "driveFile" in m:
            f = m["driveFile"].get("driveFile", {})
            lines.append(f"- attached file: {f.get('title', '?')} {f.get('alternateLink', '')}".rstrip())
        elif "link" in m:
            lines.append(f"- link: {m['link'].get('title') or ''} {m['link'].get('url', '')}".strip())
        elif "youtubeVideo" in m:
            y = m["youtubeVideo"]
            lines.append(f"- video: {y.get('title', '')} {y.get('alternateLink', '')}".strip())
        elif "form" in m:
            lines.append(f"- form: {m['form'].get('title', '')} {m['form'].get('formUrl', '')}".strip())
    if cw.get("alternateLink"):
        lines.append(f"Open in Classroom: {cw['alternateLink']}")
    return "\n".join(lines)


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
                            ) -> tuple[list[dict[str, Any]], set[str], list[str]]:
    """Work he still owes, inside the window, as records rather than prose.

    ``list_assignments`` renders these for the model and the task board takes
    the fields, so both call this and cannot disagree. Returns (assignments,
    all outstanding coursework ids, courses that could not be read). The ids
    deliberately include owed work outside the display window: an old item is
    hidden from the phone, not falsely treated as submitted.
    """
    svc = _service(account, "classroom", "v1")
    courses = {c["id"]: c.get("name", "?")
               for c in svc.courses().list(courseStates=["ACTIVE"]).execute().get("courses", [])}
    floor = (clock.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    ceiling = (clock.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    out: list[dict[str, Any]] = []
    outstanding_ids: set[str] = set()
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
            outstanding_ids.update(owed)
            work = _all_coursework(svc, cid)
        except Exception as e:  # noqa: BLE001
            # One locked-down course must not blank out the others.
            refused.append(f"{tidy_course(name)} ({type(e).__name__})")
            continue

        for cw in work:
            if cw.get("id") not in owed:
                continue
            _, due = _due_key(cw, name)
            if REQUIRE_DUE_DATE and not due:
                continue
            if not (floor <= due <= ceiling):
                continue
            # The clock travels with the date. A deadline at 08:30 is the
            # previous evening's work and a bare date is not, and a board that
            # shows only "09-22" cannot tell him which he is looking at.
            at = classroom_due(cw)
            out.append({"id": cw["id"], "title": cw.get("title") or "(untitled)",
                        "course": tidy_course(name), "due": due, "courseId": cid,
                        "due_at": at.isoformat() if at else "",
                        "due_precision": due_precision(cw)})
    out.sort(key=lambda a: a["due"])
    return out, outstanding_ids, refused


#: Posts older than this are history, not homework. v1's number.
MATERIAL_DAYS_BACK = 7


def _post_text(item: dict[str, Any]) -> str:
    """The readable body of an announcement or a material post.

    Title and body together: AP Lang's title is the day ("WEEK 6 - WED 9/16")
    and the body is the work, so either alone is useless.
    """
    text = (item.get("text") or item.get("description") or "").strip()
    title = (item.get("title") or "").strip()
    if title and title.lower() not in text.lower():
        text = f"{title}\n{text}".strip()
    return text


def recent_materials(account: str, days_back: int = MATERIAL_DAYS_BACK,
                     limit: int = 12) -> list[dict[str, Any]]:
    """Recently posted material and announcements, as context rather than tasks.

    Not every class assigns work as an *assignment*. AP Lang posts the day's
    reading as a ``courseWorkMaterial`` and some teachers just write an
    announcement. Neither has a studentSubmission, so ``outstanding_assignments``
    skips the course entirely and the class reads as having no homework — which
    is the single worst thing this board can say, because he believes it.

    Deliberately not turned into tasks, which is v1's rule and his: a post is
    prose, and inventing an assignment out of it is the one thing he has asked
    Argon never to do. A scraper that minted tasks from the "HW:" block put the
    same AP Lang work on the board twice under two spellings, because the
    teacher had also created it properly as coursework. These are context for
    answering "what do I have for Lang", not commitments.
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
             _post_text),
            ("announcement", svc.courses().announcements(),
             "announcementStates", "announcements",
             _post_text),
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
                raw = titler(item)
                body = " ".join(raw.split())
                if not body:
                    continue
                # `text` keeps the line breaks. The planner reads AP Lang's
                # "HW:" block as numbered lines, and the flattened, truncated
                # `title` loses both the lines and, on a long post, the block.
                out.append({"course": tidy_course(name), "kind": kind,
                            "title": body[:300], "text": raw[:4000], "at": at})
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
    items, _, refused = outstanding_assignments(account, days_back)
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

    ``dueDate`` and ``dueTime`` are one instant in UTC, converted to local.
    That is what the API documents and, more usefully, what his Classroom
    actually shows: "Chapter 6 Key terms" comes back as
    ``dueDate=2026-09-18, dueTime=06:59Z`` and Classroom displays it as due
    **Sep 17, 11:59 PM** — 06:59Z is 23:59 the previous day in Pacific.

    Reading the date from ``dueDate`` and only the clock from ``dueTime``
    reconstructs "Sep 18, 11:59 PM", which is a day late and was wrong on every
    timed assignment. The two fields are not independent; 06:59Z only looks
    like an odd hour because it is midnight somewhere else.

    With no ``dueTime`` there is no official instant, so local end-of-day is a
    work-by fallback — and *that* one is a bare calendar date, not UTC.
    """
    due_date = coursework.get("dueDate")
    if not due_date:
        return None
    due_time = coursework.get("dueTime")
    try:
        if due_time is None:
            return datetime(due_date["year"], due_date["month"], due_date["day"],
                            23, 59, tzinfo=clock.TZ)
        return datetime(
            due_date["year"], due_date["month"], due_date["day"],
            due_time.get("hours", 0), due_time.get("minutes", 0),
            due_time.get("seconds", 0), due_time.get("nanos", 0) // 1000,
            tzinfo=UTC,
        ).astimezone(clock.TZ)
    except (KeyError, TypeError, ValueError):
        return None


#: Classes that take the work in class on the date Classroom shows, whatever
#: time Classroom attaches. Math, Japanese and AP Lang post "11:59 PM" but
#: collect at the start of the lesson, so the work is the night before. His
#: list (Lang added 09-22), carried from v1's CLASS_DUE_OFFSETS_DAYS; matched as
#: a substring of the course name.
#:
#: The 09-22 rewrite keyed this on deadline *shape* instead (date-only means the
#: night before). His Math and Japanese work is never date-only — every item
#: comes back at 23:59 — so the rule never fired for the two classes it was for.
COLLECTED_IN_CLASS = ("math analysis", "japanese", "english lang")

#: A deadline before this hour is met by working the evening before: 08:30 test
#: corrections, AI's 00:01 exercises. School is out by 15:36 on a regular day.
WORK_NIGHT_CUTOFF_HOUR = 16


def classroom_task_date(coursework: dict[str, Any], course: str = "") -> str:
    """The evening he works on it, which is the date the board shows.

    The night before the deadline when the work is collected in class, when
    the deadline falls before the end of school, or when Classroom gives only
    a date. Otherwise the deadline's own local date: a 23:59 Physics upload is
    that evening's work.
    """
    due = classroom_due(coursework)
    if due is None:
        return ""
    in_class = any(c in course.lower() for c in COLLECTED_IN_CLASS)
    if (in_class or coursework.get("dueTime") is None
            or due.hour < WORK_NIGHT_CUTOFF_HOUR):
        due -= timedelta(days=1)
    return due.strftime("%Y-%m-%d")


def due_precision(coursework: dict[str, Any]) -> str:
    """"instant" when the teacher set a time, "work_by_day" when only a date.

    v1 kept these apart and this rewrite collapsed both to a bare date, which
    is what made morning deadlines look a day late: "9/21 MCQ Monday Test
    Corrections" showed as due 09-22 with no hint that 09-22 means 08:30, so
    it read as an evening's work on the Tuesday rather than something to finish
    on the Monday night. The date was right; the missing half was the clock.
    """
    if not coursework.get("dueDate"):
        return ""
    return "instant" if coursework.get("dueTime") is not None else "work_by_day"


def _due_key(work: dict[str, Any], course: str = "") -> tuple:
    """Sort by local due date, undated last — the order he reads them in."""
    due = classroom_task_date(work, course)
    return (1, "") if not due else (0, due)


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
        out.append(f"- {h.get('Subject', '(no subject)')} — {h.get('From', '?')}"
                   f" [id: {m['id']}]")
    return "\n".join(out)


#: Output bounds for read_mail. The text is untrusted and goes to the model.
MAIL_BODY_CHARS = 6000
MAIL_HEADER_CHARS = 300
MAIL_MAX_ATTACHMENTS = 20
_MAIL_HEADERS = ("From", "To", "Cc", "Subject", "Date")


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def _html_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    p.close()
    return "".join(p.out)


def _decode_part(part: dict[str, Any]) -> str:
    data = (part.get("body") or {}).get("data") or ""
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    ctype = next((h["value"] for h in part.get("headers", [])
                  if h["name"].lower() == "content-type"), "")
    m = re.search(r'charset="?([\w.-]+)', ctype, re.I)
    try:
        return raw.decode(m.group(1) if m else "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _walk_mail(part: dict[str, Any], found: dict[str, list], depth: int = 0) -> None:
    if depth > 10:  # a hostile message can nest MIME arbitrarily deep
        return
    body = part.get("body") or {}
    mime = part.get("mimeType", "")
    if part.get("filename") or body.get("attachmentId"):
        found["files"].append({"attachmentId": body.get("attachmentId", ""),
                               "filename": part.get("filename", ""),
                               "mimeType": mime, "size": body.get("size", 0)})
    elif mime in ("text/plain", "text/html") and body.get("data"):
        found[mime].append(_decode_part(part))
    for sub in part.get("parts") or []:
        _walk_mail(sub, found, depth + 1)


def _clean(value: str, limit: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))[:limit]


def read_mail(account: str, message_id: str) -> str:
    """One message in full: headers, text body, attachment metadata. Read-only.

    The result is attacker-controlled text; the caller fences it as untrusted.
    Attachment contents are not fetched, only listed with their attachmentId.
    """
    if not re.fullmatch(r"[0-9A-Za-z_-]{1,64}", message_id or ""):
        return "That is not a Gmail message id."
    msg = _service(account, "gmail", "v1").users().messages().get(
        userId="me", id=message_id, format="full").execute()
    payload = msg.get("payload") or {}
    h = {x["name"].lower(): x["value"] for x in payload.get("headers", [])}
    found: dict[str, list] = {"files": [], "text/plain": [], "text/html": []}
    _walk_mail(payload, found)
    body = "\n".join(found["text/plain"]).strip()
    if not body:
        body = _html_text("\n".join(found["text/html"])).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    lines = [f"{n}: {_clean(h[n.lower()], MAIL_HEADER_CHARS)}"
             for n in _MAIL_HEADERS if n.lower() in h]
    lines.append(f"Message id: {_clean(message_id, 64)}")
    files = found["files"]
    for f in files[:MAIL_MAX_ATTACHMENTS]:
        lines.append(f"Attachment: name={_clean(f['filename'], 150)!r} "
                     f"mime={_clean(f['mimeType'], 80)} size={f['size']} "
                     f"attachmentId={_clean(f['attachmentId'], 200)}")
    if len(files) > MAIL_MAX_ATTACHMENTS:
        lines.append(f"(+{len(files) - MAIL_MAX_ATTACHMENTS} more attachments)")
    if len(body) > MAIL_BODY_CHARS:
        body = body[:MAIL_BODY_CHARS] + "\n(truncated)"
    lines += ["", body or "(no text body)"]
    return "\n".join(lines)


def _selftest() -> None:
    """Formatting and the failure path only; the rest needs real credentials."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ARGON_HOME"] = tmp

        # Ground truth from his own Classroom, which this got wrong twice in
        # both directions. "Chapter 6 Key terms" is dueDate=2026-09-18 with
        # dueTime=06:59Z and Classroom shows it as due Sep 17, 11:59 PM.
        chapter6 = {"dueDate": {"year": 2026, "month": 9, "day": 18},
                    "dueTime": {"hours": 6, "minutes": 59}}
        assert classroom_due(chapter6).strftime("%Y-%m-%d %H:%M") == "2026-09-17 23:59", \
            "dueDate+dueTime is one UTC instant, not a local date plus a clock"

        # An 08:30 local deadline stays on its own day — the conversion must not
        # shift everything back by a day to make the midnight case work.
        review = {"dueDate": {"year": 2026, "month": 9, "day": 18},
                  "dueTime": {"hours": 15, "minutes": 30}}
        assert classroom_due(review).strftime("%Y-%m-%d %H:%M") == "2026-09-18 08:30"

        # No dueTime is a bare calendar date, so end-of-day *local*.
        undated = {"dueDate": {"year": 2026, "month": 9, "day": 20}}
        assert classroom_due(undated).strftime("%Y-%m-%d %H:%M") == "2026-09-20 23:59"
        assert classroom_task_date(undated) == "2026-09-19", \
            "date-only homework belongs on the previous evening's board"
        assert classroom_task_date(review) == "2026-09-17", \
            "an 08:30 deadline is the previous evening's work"

        # His real board on 09-22. Every item here is timed 23:59 Pacific
        # (06:59Z the next day), so only the course decides the evening.
        def eleven59(day: int) -> dict[str, Any]:
            return {"dueDate": {"year": 2026, "month": 9, "day": day + 1},
                    "dueTime": {"hours": 6, "minutes": 59}}
        assert classroom_task_date(eleven59(23), "Math Analysis/Calc A") == "2026-09-22", \
            "HW 23 is collected in Wednesday's lesson, so it is Tuesday night's work"
        assert classroom_task_date(eleven59(24), "Japanese 4 & AP") == "2026-09-23", \
            "Japanese collects in class too"
        assert classroom_task_date(eleven59(25), "AP English Lang-Han") == "2026-09-24", \
            "so does AP Lang"
        assert classroom_task_date(eleven59(24), "Physics") == "2026-09-24", \
            "a 23:59 upload is that evening's work"
        ai = {"dueDate": {"year": 2026, "month": 9, "day": 23},
              "dueTime": {"hours": 7, "minutes": 1}}
        assert classroom_task_date(ai, "Artif Intell") == "2026-09-22", \
            "00:01 Wednesday means done by Tuesday night"
        assert classroom_due({}) is None

        # v1's distinction, which this rewrite had flattened: a deadline with a
        # time is an instant, one without is a day to work by.
        assert due_precision({"dueDate": {"year": 2026, "month": 9, "day": 22},
                              "dueTime": {"hours": 15, "minutes": 30}}) == "instant"
        assert due_precision({"dueDate": {"year": 2026, "month": 9, "day": 20}}) \
            == "work_by_day"
        assert due_precision({}) == ""

        # An assignment's body comes back, with its attachments as links.
        shown = render_coursework({
            "title": "HW 25", "description": "p. 212 #3-19 odd, #24",
            "materials": [{"driveFile": {"driveFile": {"title": "Textbook Exercises",
                                                       "alternateLink": "https://d/x"}}}],
            "alternateLink": "https://classroom/hw25"})
        assert "#3-19 odd" in shown and "Textbook Exercises https://d/x" in shown
        assert "(no written instructions)" in render_coursework({"title": "HW 26"})

        # A post carries its title and its body, because either alone is
        # useless: AP Lang's title is the day and the body is the work.
        assert _post_text({"title": "WEEK 6 - WED 9/16",
                           "description": "HW: Read Act 3"}) \
            == "WEEK 6 - WED 9/16\nHW: Read Act 3"
        assert _post_text({"text": "Chapter 2 InQuizitive due tonight"}) \
            == "Chapter 2 InQuizitive due tonight"
        # A title already inside the body is not repeated.
        assert _post_text({"title": "Quiz", "text": "Quiz on Friday"}) == "Quiz on Friday"

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


        # -- per-thread services, fake build, no network --------------------
        import sys
        import types
        built: list[object] = []
        fake = types.ModuleType("googleapiclient.discovery")
        fake.build = lambda *a, **k: built.append(object()) or built[-1]
        saved = {k: sys.modules.get(k) for k in ("googleapiclient", "googleapiclient.discovery")}
        sys.modules["googleapiclient"] = types.ModuleType("googleapiclient")
        sys.modules["googleapiclient.discovery"] = fake
        real_creds = globals()["_credentials"]
        globals()["_credentials"] = lambda account: object()
        try:
            a = _service("work", "gmail", "v1")
            assert _service("work", "gmail", "v1") is a, "same thread reuses its service"
            seen: list[object] = []
            t = threading.Thread(target=lambda: seen.append(_service("work", "gmail", "v1")))
            t.start(); t.join()
            assert seen[0] is not a, "another thread must not share the connection"
            # An external reauthorisation (the CLI process) rewrites the token
            # file; no local counter is touched, yet the service must rebuild.
            token_path("work").write_text(json.dumps({"scopes": [], "rotated": 1}))
            b = _service("work", "gmail", "v1")
            assert b is not a, "external token rewrite must rebuild the service"
            assert _service("work", "gmail", "v1") is b, "unchanged token does not rebuild"
            assert _service("work", "gmail", "v1") is not seen[0]
            n = len(built)
            _service("work", "gmail", "v1")
            assert len(built) == n, "no rebuild without a token change"
            # A token rewrite during construction must not be mistaken for the
            # token used by the service just built.
            def rotate_during_build(*a, **k):
                token_path("race").write_text('{"rotated": true}')
                built.append(object())
                return built[-1]
            fake.build = rotate_during_build
            old = _service("race", "gmail", "v1")
            fake.build = lambda *a, **k: built.append(object()) or built[-1]
            assert _service("race", "gmail", "v1") is not old
        finally:
            globals()["_credentials"] = real_creds
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        # -- Gmail read, via a fake service ---------------------------------
        def b64(t: str) -> str:
            return base64.urlsafe_b64encode(t.encode()).decode().rstrip("=")

        class _Call:
            def __init__(self, v): self.v = v
            def execute(self): return self.v

        class _Msgs:
            def __init__(self, full): self.full, self.calls = full, []
            def list(self, **k): return _Call({"messages": [{"id": "m1"}]})
            def get(self, **k):
                self.calls.append(k)
                if k["format"] == "metadata":
                    return _Call({"payload": {"headers": [
                        {"name": "Subject", "value": "Lab"}, {"name": "From", "value": "j@x"}]}})
                return _Call(self.full)

        def fake_gmail(full):
            msgs = _Msgs(full)
            svc = types.SimpleNamespace(users=lambda: types.SimpleNamespace(messages=lambda: msgs))
            return svc, msgs

        full = {"payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "Subject", "value": "Lab\nBcc: evil"},
                        {"name": "From", "value": "Mr J <j@x>"}],
            "parts": [
                {"mimeType": "multipart/alternative", "parts": [
                    {"mimeType": "text/plain", "body": {"data": b64("Bring goggles")}},
                    {"mimeType": "text/html", "body": {"data": b64("<p>HTML only</p>")}}]},
                {"mimeType": "application/pdf", "filename": "lab.pdf",
                 "body": {"attachmentId": "ATT1", "size": 42}}]}}
        svc, msgs = fake_gmail(full)
        real_service = globals()["_service"]
        globals()["_service"] = lambda *a: svc
        try:
            assert "[id: m1]" in search_mail("work", "lab"), "search must expose message ids"
            got = read_mail("work", "m1")
            assert "Bring goggles" in got and "HTML only" not in got, "plain text preferred"
            assert "Subject: Lab Bcc: evil" in got, "header newlines cannot forge headers"
            assert "name='lab.pdf' mime=application/pdf size=42 attachmentId=ATT1" in got, got
            assert msgs.calls[-1] == {"userId": "me", "id": "m1", "format": "full"}
            assert "not a Gmail message id" in read_mail("work", "../x")

            html_only = {"payload": {"mimeType": "text/html", "headers": [],
                                     "body": {"data": b64("<style>x{}</style><p>Hi</p><script>bad()</script><div>there</div>")}}}
            svc, msgs = fake_gmail(html_only)
            got = read_mail("work", "m2")
            assert "Hi" in got and "there" in got and "bad()" not in got and "x{}" not in got, got

            big = {"payload": {"mimeType": "multipart/mixed", "headers": [],
                               "parts": [{"mimeType": "text/plain", "body": {"data": b64("a" * 20000)}}]
                               + [{"mimeType": "x/y", "filename": f"f{i}", "body": {"attachmentId": f"A{i}"}}
                                  for i in range(30)]}}
            svc, msgs = fake_gmail(big)
            got = read_mail("work", "m3")
            assert len(got) < MAIL_BODY_CHARS + 6000 and "(truncated)" in got
            assert got.count("Attachment:") == MAIL_MAX_ATTACHMENTS and "+10 more" in got
        finally:
            globals()["_service"] = real_service

        del os.environ["ARGON_HOME"]
    print("google selftest ok")


if __name__ == "__main__":
    _selftest()
