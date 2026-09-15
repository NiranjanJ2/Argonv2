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
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG = Path.home() / ".argon-widget.json"
TIMEOUT = 4.0


def config() -> dict:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {"base": os.environ.get("ARGON_BASE", "http://localhost:3995"),
            "token": os.environ.get("ARGON_TOKEN", "")}


def call(path: str, method: str = "GET", body: dict | None = None) -> dict:
    cfg = config()
    req = urllib.request.Request(
        cfg["base"].rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {cfg['token']}",
                 "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def fetch() -> dict:
    try:
        return call("/v2/state")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"error": str(e)}


# -- what the readout says --------------------------------------------------

def _due_label(due: str | None, today: str) -> str:
    if not due:
        return ""
    day = due[:10]
    if day < today:
        return "overdue"
    if day == today:
        return "today"
    return day[5:]  # MM-DD


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

    if started:
        title = f"▶ {started[0]['title'][:24]}"
    elif overdue:
        title = f"argon {len(overdue)}!"
    elif tasks:
        title = f"argon {len(tasks)}"
    else:
        title = "argon ✓"

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
        for verb, text in (("start", "Start"), ("complete", "Done")):
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
    assert v["title"] == "argon 1!", v["title"]
    assert "2 unread" in v["lines"] and "$0.29 of $5 this month" in v["lines"]

    # A started task takes the title over an overdue count.
    state["tasks"][1]["started_at"] = "2026-09-14T18:00:00"
    v = build_view(state, now=now)
    assert v["title"].startswith("▶ Today thing"), v["title"]

    # Nothing open reads as done, not as an error.
    v = build_view({"tasks": [], "ticking": False, "budget": {}}, now=now)
    assert v["title"] == "argon ✓" and "not watching right now" in v["lines"]

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
