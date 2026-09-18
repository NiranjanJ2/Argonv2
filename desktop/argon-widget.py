#!/usr/bin/env python3
"""Argon on the Mac: a SwiftBar menu bar item and an Übersicht widget.

Both readouts shell out to this one file, so they cannot disagree and adding a
field means editing one place.  ``build_view`` decides what the readout *says*;
the renderers only decide how it looks.

    argon-widget.py              SwiftBar plugin format
    argon-widget.py --json       Übersicht (argon.jsx renders it)
    argon-widget.py --selftest   asserts, no network
    argon-widget.py --do start <task-id>

Actions go through the same HTTP surface the phone uses. They deliberately do
not touch the database directly: a task completed from the menu bar should be
indistinguishable from one completed by asking Argon.

Config: ~/.argon-widget.json — {"base": "http://agentneon:3995", "token": "..."}
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG = Path.home() / ".argon-widget.json"
TIMEOUT = 4.0


def config() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text())
        except (OSError, json.JSONDecodeError) as e:
            # Falling through to the env default silently is how a widget ends
            # up reporting a DNS failure for a host nobody configured.
            _trace(f"config unreadable: {e!r}")
    return {"base": os.environ.get("ARGON_BASE", "http://localhost:3995"),
            "token": os.environ.get("ARGON_TOKEN", "")}


def _trace(line: str) -> None:
    """One line into ~/.argon/widget.log.

    The host runs this, not a terminal: when SwiftBar or Übersicht shows an
    error there is no way to ask the process what it tried. This is how the
    widget's own run gets inspected afterwards instead of guessed at.
    """
    try:
        with (pathlib.Path.home() / ".argon" / "widget.log").open("a") as f:
            f.write(f"{datetime.now():%F %T} {line}\n")
    except OSError:
        pass


def call(path: str, method: str = "GET", body: dict | None = None) -> dict:
    cfg = config()
    req = urllib.request.Request(
        cfg["base"].rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        # A real User-Agent. urllib sends "Python-urllib/3.x" by default and
        # Cloudflare answers that with 403 before the request ever reaches the
        # tunnel — so the widget read "unreachable" while curl, from the same
        # machine with the same token, got a 200.
        headers={"Authorization": f"Bearer {cfg['token']}",
                 "Content-Type": "application/json",
                 "User-Agent": "Argon-Widget/2 (macOS)"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def fetch() -> dict:
    base = config().get("base", "?")
    try:
        state = call("/v2/state")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Name the host in the message. "nodename nor servname provided" says
        # a lookup failed but not what was looked up, which is useless when the
        # widget is configured somewhere you are not looking.
        _trace(f"FAIL base={base} argv={sys.argv[1:]} py={sys.executable} err={e!r}")
        return {"error": f"{e} [{base}]"}
    _trace(f"ok base={base} argv={sys.argv[1:]}")
    return state


# -- what the readout says --------------------------------------------------

def _due_label(due: str | None, today: str) -> str:
    if not due:
        return ""
    day = due[:10]
    if day < today:
        return "overdue"
    if day == today:
        return "today"
    # Tomorrow is tonight's work. He does homework the evening before it is
    # collected, so folding "due tomorrow" in with next week's reading hides
    # the thing he is actually about to sit down to.
    if day == _tomorrow(today):
        return "tomorrow"
    return day[5:]  # MM-DD


def _tomorrow(today: str) -> str:
    return (datetime.strptime(today, "%Y-%m-%d")
            + timedelta(days=1)).strftime("%Y-%m-%d")


def build_view(state: dict, *, now: datetime | None = None) -> dict:
    """Everything the readouts display.  Pure: no network, no clock surprises."""
    now = now or datetime.now()
    if state.get("error"):
        return {"title": "argon ?", "lines": [f"unreachable: {state['error']}"],
                "tasks": [], "ok": False}

    today = now.strftime("%Y-%m-%d")
    tasks = [t for t in state.get("tasks", []) if not t.get("done")]

    def sort_key(t: dict) -> tuple:
        due = (t.get("due") or "9999-99-99")[:10]
        return (0 if due < today else 1 if due == today else 2, due, t.get("title", ""))

    tasks = sorted(tasks, key=sort_key)
    overdue = [t for t in tasks if (t.get("due") or "")[:10] < today and t.get("due")]
    started = [t for t in tasks if t.get("started_at")]

    # State first, count second. "argon 2!" told him a number without telling
    # him what it was a number of, and said nothing at all about whether he was
    # working — which is the one thing a menu bar is for.
    tonight = [t for t in tasks
               if _due_label(t.get("due"), today) in ("overdue", "today", "tomorrow")]
    if started:
        title = f"▶ {started[0]['title'][:24]}"
    elif tonight:
        title = f"Idle · {len(tonight)} tonight"
    elif tasks:
        title = f"Idle · {len(tasks)} open"
    else:
        title = "Idle · clear"

    lines = []
    school = state.get("school") or {}
    if school.get("period"):
        lines.append(f"In {school['period']}")
    elif school.get("schedule"):
        lines.append(school["schedule"])
    budget = state.get("budget") or {}
    if budget.get("cap"):
        lines.append(f"${budget.get('spent', 0):.2f} of ${budget['cap']:.0f} this month")
    if state.get("unread"):
        lines.append(f"{state['unread']} unread")
    if not state.get("ticking"):
        lines.append("not watching right now")

    return {
        "title": title,
        "lines": lines,
        "ok": True,
        "tasks": [{
            "id": t["id"],
            "title": t.get("title", ""),
            "due": _due_label(t.get("due"), today),
            "started": bool(t.get("started_at")),
            "subject": t.get("subject") or "",
        } for t in tasks[:12]],
    }


# -- renderers --------------------------------------------------------------

def render_swiftbar(view: dict) -> str:
    me = Path(__file__).resolve()
    out = [view["title"], "---"]
    out += view["lines"] + (["---"] if view["lines"] else [])
    if not view["tasks"]:
        out.append("Nothing open")
    for t in view["tasks"]:
        mark = "▶ " if t["started"] else ""
        label = f"{mark}{t['title']}" + (f"  ({t['due']})" if t["due"] else "")
        out.append(label)
        # A running task offers Stop; a waiting one offers Start. Showing both
        # on both invites the wrong one at the moment he is trying to stop.
        verbs = (("stop", "Stop"), ("complete", "Done")) if t["started"] \
            else (("start", "Start"), ("complete", "Done"))
        for verb, text in verbs:
            out.append(f"--{text} | bash={me} param1=--do param2={verb} "
                       f"param3={t['id']} terminal=false refresh=true")
    out.append("---")
    out.append(f"Refresh | refresh=true")
    return "\n".join(out)


def render_json(view: dict) -> str:
    return json.dumps(view, indent=2)


# -- actions ----------------------------------------------------------------

def do(verb: str, task_id: str = "") -> str:
    if verb == "start":
        return str(call(f"/v1/tasks/{task_id}", "PATCH", {"started": True}))
    if verb == "complete":
        return str(call(f"/v1/tasks/{task_id}", "PATCH", {"done": True}))
    if verb == "stop":
        # Putting it down is not finishing it. Sends started=false, which drops
        # the shield the start raised without claiming the work is done.
        return str(call(f"/v1/tasks/{task_id}", "PATCH", {"started": False}))
    if verb == "add":
        script = 'display dialog "New task" default answer ""'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        title = r.stdout.split("text returned:")[-1].strip()
        return str(call("/v1/tasks", "POST", {"title": title})) if title else "cancelled"
    if verb == "say":
        return str(call("/v2/say", "POST", {"text": task_id, "source": "desktop"}))
    return f"unknown action {verb!r}"


def selftest() -> None:
    now = datetime(2026, 9, 14, 18, 0)

    down = build_view({"error": "connection refused"}, now=now)
    assert down["ok"] is False and "unreachable" in down["lines"][0]
    assert down["tasks"] == []

    state = {
        "school": {"schedule": "Regular (M/Th/F); school ends 15:36", "period": None},
        "ticking": True, "unread": 2,
        "budget": {"spent": 0.29, "cap": 5.0},
        "tasks": [
            {"id": "a", "title": "Late thing", "due": "2026-09-01"},
            {"id": "b", "title": "Today thing", "due": "2026-09-14"},
            {"id": "c", "title": "Later thing", "due": "2026-09-20"},
            {"id": "d", "title": "No due"},
            {"id": "e", "title": "Done thing", "done": True},
        ],
    }
    v = build_view(state, now=now)
    assert v["ok"] and [t["id"] for t in v["tasks"]] == ["a", "b", "c", "d"], v["tasks"]
    assert v["tasks"][0]["due"] == "overdue" and v["tasks"][1]["due"] == "today"
    assert v["tasks"][2]["due"] == "09-20" and v["tasks"][3]["due"] == ""
    assert v["title"] == "Idle · 2 tonight", v["title"]
    assert "2 unread" in v["lines"] and "$0.29 of $5 this month" in v["lines"]

    # A started task takes the title over an overdue count.
    state["tasks"][1]["started_at"] = "2026-09-14T18:00:00"
    v = build_view(state, now=now)
    assert v["title"].startswith("▶ Today thing"), v["title"]

    # Nothing open reads as done, not as an error.
    v = build_view({"tasks": [], "ticking": False, "budget": {}}, now=now)
    assert v["title"] == "Idle · clear" and "not watching right now" in v["lines"]

    # The title says what he is doing before it says how much there is. A bare
    # count ("argon 2!") is a number with no noun and no state.
    late_only = build_view({"tasks": [{"id": "a", "title": "Late", "due": "2026-09-01"}]},
                           now=now)
    assert late_only["title"] == "Idle · 1 tonight", late_only["title"]
    ahead = build_view({"tasks": [{"id": "z", "title": "Later", "due": "2026-09-30"}]},
                       now=now)
    assert ahead["title"] == "Idle · 1 open", ahead["title"]

    out = render_swiftbar(build_view(state, now=now))
    assert out.splitlines()[1] == "---" and "param2=complete" in out
    json.loads(render_json(build_view(state, now=now)))
    assert do("nonsense") == "unknown action 'nonsense'"
    print("widget selftest ok")


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        selftest()
        return 0
    if "--do" in argv:
        i = argv.index("--do")
        print(do(*argv[i + 1:i + 3]))
        return 0
    view = build_view(fetch())
    print(render_json(view) if "--json" in argv else render_swiftbar(view))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
