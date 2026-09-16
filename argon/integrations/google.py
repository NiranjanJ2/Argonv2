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
import re
from datetime import datetime, timedelta
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
    from googleapiclient.discovery import build

    return build(api, version, credentials=_credentials(account),
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
    if not events:
        return "Nothing on the calendar."
    return "\n".join(f"- {_when(e)} — {e.get('summary') or '(untitled)'}" for e in events)


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

#: How far back a due date can be and still be worth showing. His Classroom
#: goes back to 2022 and nobody is doing the 2022 counselling form; sorting by
#: due date ascending and taking the first thirty returns exactly that.
STALE_AFTER_DAYS = 21


def outstanding_assignments(account: str, days_back: int = STALE_AFTER_DAYS
                            ) -> tuple[list[dict[str, Any]], list[str]]:
    """Work he still owes, as records rather than prose.

    ``list_assignments`` renders these for the model; the task board needs the
    fields, so both call this. Returns (assignments, courses that refused).
    """
    svc = _service(account, "classroom", "v1")
    courses = {c["id"]: c.get("name", "?")
               for c in svc.courses().list(courseStates=["ACTIVE"]).execute().get("courses", [])}
    cutoff = (clock.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

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
            work = svc.courses().courseWork().list(
                courseId=cid, courseWorkStates=["PUBLISHED"],
                orderBy="dueDate desc", pageSize=100).execute()
        except Exception as e:  # noqa: BLE001
            refused.append(f"{name} ({type(e).__name__})")
            continue

        for cw in work.get("courseWork", []):
            if cw.get("id") not in owed:
                continue
            _, due = _due_key(cw)
            if due and due < cutoff:
                continue
            out.append({"id": cw["id"], "title": cw.get("title") or "(untitled)",
                        "course": tidy_course(name), "due": due, "courseId": cid})
    out.sort(key=lambda a: (a["due"] == "", a["due"]))
    return out, refused


def list_assignments(account: str, limit: int = 30, days_back: int = STALE_AFTER_DAYS) -> str:
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


def _due_key(work: dict[str, Any]) -> tuple:
    """Sort by due date, undated last — the order he reads them in."""
    due = work.get("dueDate") or {}
    if not due.get("year"):
        return (1, "")
    return (0, f"{due['year']}-{due['month']:02d}-{due['day']:02d}")


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
