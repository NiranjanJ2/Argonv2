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
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

from argon import clock, config

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]


class GoogleUnavailable(RuntimeError):
    """Raised when an account cannot be used.  Rendered to the model as text."""


def token_path(account: str):
    return config.path("google", f"{account}.json")


def client_secret_path():
    return config.path("google", "client_secret.json")


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

    creds = Credentials.from_authorized_user_file(str(p), SCOPES)
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
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path()), SCOPES)
    creds = flow.run_local_server(port=port, open_browser=False)
    token_path(account).write_text(creds.to_json())
    _service.cache_clear()
    return f"connected {account}"


def status(accounts: list[str]) -> str:
    """Exercise each grant for real.  Never trusts the token file alone."""
    lines = []
    for name in accounts:
        try:
            _service(name, "calendar", "v3").calendarList().list(maxResults=1).execute()
            lines.append(f"{name}: ok")
        except GoogleUnavailable as e:
            lines.append(f"{name}: {e}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"{name}: failed ({type(e).__name__}: {e})")
    return "\n".join(lines) or "no Google accounts configured"


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


def list_assignments(account: str, limit: int = 30) -> str:
    svc = _service(account, "classroom", "v1")
    courses = {c["id"]: c.get("name", "?")
               for c in svc.courses().list(courseStates=["ACTIVE"]).execute().get("courses", [])}
    items: list[dict[str, Any]] = []
    for cid in courses:
        work = svc.courses().courseWork().list(
            courseId=cid, orderBy="dueDate asc", pageSize=limit).execute()
        items.extend(work.get("courseWork", []))
    return format_assignments(items[:limit], courses)


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

        # A missing account explains itself rather than disappearing.
        try:
            _credentials("nobody")
            raise AssertionError("should have raised")
        except GoogleUnavailable as e:
            assert "not connected" in str(e) and "google-auth nobody" in str(e)

        assert status([]) == "no Google accounts configured"
        assert "not connected" in status(["nobody"])

        del os.environ["ARGON_HOME"]
    print("google selftest ok")


if __name__ == "__main__":
    _selftest()
