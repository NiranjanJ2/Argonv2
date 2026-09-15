"""HTTP surface: a frozen /v1 and a clean /v2.

``/v1`` exists so the build already on his phone keeps working while the Swift
layer is rewritten.  Its shapes are copied from what the app decodes today, not
from what they should have been — the app is authoritative for /v1 and this
file is not allowed an opinion about it.  ``/v2`` is the surface the new client
uses, and /v1 is deleted when nothing calls it.

The two halves of this system have drifted before: the app called
``/v1/messages`` and ``/v1/ios/read`` for months before either route existed.
``_selftest`` therefore asserts the /v1 response *keys*, so a rename here fails
here rather than in a TestFlight build.
"""

from __future__ import annotations

import hmac
from functools import wraps
from typing import Any

from flask import Flask, g, jsonify, request

from argon import bell, budget, clock, schedule

#: Messages returned to the app in one page.
MESSAGE_LIMIT = 50

#: Ceiling on anything he types. The body limit is 1 MB, but a 300 KB paste
#: lands in the two-day window and is re-sent on every tick for two days —
#: usually over the model's context limit, so every call 400s until it ages out.
MAX_MESSAGE_CHARS = 8_000
MAX_TITLE_CHARS = 500


def create_app(rt) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1_000_000

    def require_token(view):
        @wraps(view)
        def wrapped(*a, **kw):
            token = rt.cfg.api.token
            if not token:
                return jsonify({"error": "api token not configured"}), 503
            sent = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            # Compare bytes: compare_digest raises TypeError on non-ASCII str,
            # so a unicode header turned an unauthenticated request into a 500.
            if not hmac.compare_digest(sent.encode("utf-8", "replace"),
                                       token.encode("utf-8", "replace")):
                return jsonify({"error": "unauthorized"}), 401
            return view(*a, **kw)
        return wrapped

    def body() -> dict[str, Any]:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def text_field(data: dict[str, Any], key: str) -> str:
        """A string field from untrusted JSON. `{"title": 123}` used to 500."""
        value = data.get(key)
        return value.strip() if isinstance(value, str) else ""

    def int_field(data: dict[str, Any], key: str, default: int) -> int | None:
        """An int field. None means the caller sent something unusable."""
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def task_json(t) -> dict[str, Any]:
        """Exactly the keys ArgonTask decodes. Do not tidy these names."""
        return {
            "id": t.id, "title": t.title, "done": t.done, "priority": t.priority,
            "source": t.source, "subject": t.subject, "notes": None, "due": t.due,
            "classroom_id": None, "time_estimate_min": None,
            "time_actual_min": None, "started_at": t.started_at,
        }

    def dashboard_state() -> dict[str, Any]:
        started = [t for t in rt.store.tasks() if t.started]
        return {
            "mode": "working" if started else "idle",
            "current_task": started[0].title if started else None,
            "work_session_minutes": 0,
            "lock_in_minutes": 0,
        }

    # -- health -----------------------------------------------------------
    @app.get("/health")
    def health():
        return jsonify({
            "ok": True,
            "now": clock.now().isoformat(),
            "ticking": schedule.should_tick(),
            "spend": budget.month()["usd"],
            "cap": rt.cfg.monthly_cap_usd,
        })

    # -- frozen v1 --------------------------------------------------------
    @app.post("/v1/chat")
    @require_token
    def v1_chat():
        data = body()
        text = text_field(data, "message") or text_field(data, "text")
        if not text:
            return jsonify({"error": "empty message"}), 400
        if len(text) > MAX_MESSAGE_CHARS:
            return jsonify({"error": f"message over {MAX_MESSAGE_CHARS} characters"}), 413
        out = rt.receive(text, source="ios")
        return jsonify({"reply": out.text or "", "error": out.error or None})

    @app.get("/v1/messages")
    @require_token
    def v1_messages():
        rows = [e for e in rt.transcript.window(3)
                if e.kind in ("message_in", "message_out")][-MESSAGE_LIMIT:]
        messages = [{
            "role": "user" if e.kind == "message_in" else "assistant",
            "text": e.payload.get("text", ""),
            "at": e.at,
        } for e in rows]
        return jsonify({"messages": messages, "unread": rt.unread()})

    @app.post("/v1/ios/read")
    @require_token
    def v1_read():
        rt.mark_read()
        return jsonify({"ok": True, "unread": 0})

    @app.get("/v1/status")
    @require_token
    def v1_status():
        return jsonify({
            "state": dashboard_state(),
            "schedule": bell.describe(),
            "period": bell.current_period(),
            "spend": budget.month()["usd"],
            "cap": rt.cfg.monthly_cap_usd,
        })

    @app.get("/v1/tasks")
    @require_token
    def v1_tasks():
        return jsonify({
            "tasks": [task_json(t) for t in rt.store.tasks()],
            "state": dashboard_state(),
        })

    @app.post("/v1/tasks")
    @require_token
    def v1_add_task():
        data = body()
        title = text_field(data, "title")
        if not title:
            return jsonify({"error": "title required"}), 400
        if len(title) > MAX_TITLE_CHARS:
            return jsonify({"error": f"title over {MAX_TITLE_CHARS} characters"}), 413
        t = rt.store.add_task(title, subject=data.get("subject") or "",
                              due=data.get("due") or "",
                              priority=data.get("priority") or "normal",
                              source="app")
        return jsonify({"task": task_json(t)}), 201

    @app.patch("/v1/tasks/<task_id>")
    @require_token
    def v1_update_task(task_id: str):
        data = body()
        if data.get("done") is True:
            t = rt.store.complete_task(task_id)
            return (jsonify({"task": task_json(t)}) if t
                    else (jsonify({"error": "no such open task"}), 404))
        if data.get("started") is True:
            t = rt.store.start_task(task_id)
            return (jsonify({"task": task_json(t)}) if t
                    else (jsonify({"error": "already started"}), 409))
        t = rt.store.update_task(task_id, title=data.get("title"),
                                 subject=data.get("subject"), due=data.get("due"),
                                 priority=data.get("priority"))
        return (jsonify({"task": task_json(t)}) if t
                else (jsonify({"error": "no such task"}), 404))

    @app.post("/v1/ios/register")
    @require_token
    def v1_register():
        token = text_field(body(), "token")
        if not 32 <= len(token) <= 200:
            return jsonify({"error": "bad device token"}), 400
        rt.register_device(token)
        return jsonify({"ok": True})

    @app.post("/v1/ios/state")
    @require_token
    def v1_ios_state():
        """What the phone reports about itself. Recorded as an observation the
        agent reads — never as a trigger that makes something happen."""
        data = body()
        rt.transcript.append("phone", summary=", ".join(
            f"{k}={v}" for k, v in sorted(data.items()))[:300])
        return jsonify({"ok": True})

    @app.get("/v1/ios/mode")
    @require_token
    def v1_mode_get():
        return jsonify(dashboard_state())

    @app.post("/v1/ios/mode")
    @require_token
    def v1_mode_set():
        rt.transcript.append("phone_mode", summary=str(body().get("mode", "")))
        return jsonify({"ok": True})

    @app.post("/v1/ios/override")
    @require_token
    def v1_override():
        """He asked to be let out. An override is always granted: a lock he
        cannot escape is a lock he will delete the app over."""
        minutes = int_field(body(), "minutes", 120)
        if minutes is None or not 1 <= minutes <= 24 * 60:
            return jsonify({"error": "minutes must be 1-1440"}), 400
        rt.transcript.append("override", summary=f"released for {minutes}m")
        return jsonify({"ok": True, "minutes": minutes})

    @app.route("/v1/ios/diagnostics", methods=["GET", "POST", "DELETE"])
    @require_token
    def v1_diagnostics():
        if request.method == "POST":
            rt.transcript.append("diagnostics", summary=str(body())[:300])
            return jsonify({"ok": True})
        if request.method == "DELETE":
            return jsonify({"ok": True, "entries": []})
        rows = [e.payload for e in rt.transcript.window(2) if e.kind == "diagnostics"]
        return jsonify({"entries": rows})

    @app.get("/v1/planner")
    @require_token
    def v1_planner_get():
        return jsonify({"tasks": [task_json(t) for t in rt.store.tasks()],
                        "start_at": None, "last_planned": None})

    @app.post("/v1/planner")
    @require_token
    def v1_planner_post():
        items = body().get("tasks")
        if not isinstance(items, list):
            return jsonify({"error": "tasks must be a list"}), 400
        added = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = text_field(item, "title")
            if title:
                added.append(rt.store.add_task(title[:MAX_TITLE_CHARS],
                                               due=text_field(item, "due"),
                                               source="planner"))
        return jsonify({"added": [task_json(t) for t in added]})

    @app.get("/v1/ac")
    @require_token
    def v1_ac_list():
        return jsonify({"units": rt.ac_units()})

    @app.post("/v1/ac/<mac>")
    @require_token
    def v1_ac_set(mac: str):
        return jsonify(rt.ac_set(mac, body()))

    # -- clean v2 ---------------------------------------------------------
    @app.get("/v2/state")
    @require_token
    def v2_state():
        """Everything a client needs in one call. /v1 needed four."""
        return jsonify({
            "now": clock.now().isoformat(),
            "school": {"schedule": bell.describe(), "period": bell.current_period()},
            "ticking": schedule.should_tick(),
            "tasks": [task_json(t) for t in rt.store.tasks()],
            "facts": rt.store.facts(),
            "unread": rt.unread(),
            "budget": {"spent": budget.month()["usd"], "cap": rt.cfg.monthly_cap_usd,
                       "cached_fraction": budget.cached_fraction()},
        })

    @app.get("/v2/messages")
    @require_token
    def v2_messages():
        """The transcript, optionally only what is new — `?since=<seq>`.

        Sequence numbers rather than timestamps: the client can ask for exactly
        what it has not seen without clock agreement between phone and server.
        """
        since = request.args.get("since", type=int)
        rows = (rt.transcript.since(since) if since is not None
                else rt.transcript.window(2))
        return jsonify({"messages": [
            {"seq": e.seq, "role": "user" if e.kind == "message_in" else "assistant",
             "text": e.payload.get("text", ""), "at": e.at}
            for e in rows if e.kind in ("message_in", "message_out")
        ], "unread": rt.unread()})

    @app.post("/v2/say")
    @require_token
    def v2_say():
        data = body()
        text = text_field(data, "text")
        if not text:
            return jsonify({"error": "empty message"}), 400
        if len(text) > MAX_MESSAGE_CHARS:
            # FIX: one 300 KB paste otherwise sits in the two-day window and is
            # re-sent on every tick for two days, usually over the context limit.
            return jsonify({"error": f"message over {MAX_MESSAGE_CHARS} characters"}), 413
        out = rt.receive(text, source=text_field(data, "source") or "ios")
        return jsonify({"reply": out.text or "", "spoke": out.spoke,
                        "cost": round(out.cost, 6), "error": out.error or None})

    @app.errorhandler(Exception)
    def on_error(exc: Exception):
        """Log the detail, tell the caller nothing.

        This used to return the exception type and message, so any client that
        could reach the port could read internal errors back.
        """
        code = getattr(exc, "code", 500)
        rt.transcript.append("api_error", summary=f"{request.path}: {exc!r}"[:300])
        if code == 500:
            return jsonify({"error": "internal error"}), 500
        return jsonify({"error": getattr(exc, "name", "error")}), code

    return app


def _selftest() -> None:
    import os
    import tempfile
    from datetime import datetime

    from argon import config, provider
    from argon.runtime import Runtime

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ARGON_HOME"] = tmp
        clock.set_for_test(datetime(2026, 9, 14, 18, 0, tzinfo=clock.TZ))

        cfg = config.Config()
        cfg.api.token = "secret"
        rt = Runtime(cfg)
        app = create_app(rt)
        c = app.test_client()
        auth = {"Authorization": "Bearer secret"}

        assert c.get("/health").status_code == 200
        assert c.get("/v1/tasks").status_code == 401, "token is required"
        assert c.get("/v1/tasks", headers={"Authorization": "Bearer wrong"}).status_code == 401

        # The /v1 shapes are the app's, not ours. These keys are a contract.
        t = rt.store.add_task("AP Chem pset", due="2026-09-16")
        payload = c.get("/v1/tasks", headers=auth).get_json()
        assert set(payload) == {"tasks", "state"}
        for key in ("id", "title", "done", "priority", "source", "subject", "notes",
                    "due", "classroom_id", "time_estimate_min", "time_actual_min",
                    "started_at"):
            assert key in payload["tasks"][0], key
        for key in ("mode", "current_task", "work_session_minutes", "lock_in_minutes"):
            assert key in payload["state"], key

        msgs = c.get("/v1/messages", headers=auth).get_json()
        assert set(msgs) == {"messages", "unread"}

        r = c.post("/v1/tasks", json={"title": "Read Ch 3"}, headers=auth)
        assert r.status_code == 201 and r.get_json()["task"]["title"] == "Read Ch 3"
        assert c.post("/v1/tasks", json={}, headers=auth).status_code == 400

        assert c.patch(f"/v1/tasks/{t.id}", json={"started": True},
                       headers=auth).get_json()["task"]["started_at"]
        assert c.get("/v1/status", headers=auth).get_json()["state"]["mode"] == "working"
        assert c.patch(f"/v1/tasks/{t.id}", json={"done": True}, headers=auth).status_code == 200
        assert c.patch("/v1/tasks/nope", json={"done": True}, headers=auth).status_code == 404

        assert c.post("/v1/ios/register", json={"token": "a" * 64},
                      headers=auth).status_code == 200
        assert c.post("/v1/ios/register", json={"token": "short"},
                      headers=auth).status_code == 400

        # Phone reports are observations, never triggers.
        assert c.post("/v1/ios/state", json={"shielded": True}, headers=auth).status_code == 200
        assert "phone" in [e.kind for e in rt.transcript.window(2)]

        scripted = [provider.Reply(text="two things due")]
        provider.complete = lambda *a, **k: scripted.pop(0)  # type: ignore[assignment]
        assert c.post("/v1/chat", json={"message": "what's due?"},
                      headers=auth).get_json()["reply"] == "two things due"
        assert c.post("/v1/chat", json={"message": "  "}, headers=auth).status_code == 400

        state = c.get("/v2/state", headers=auth).get_json()
        assert {"now", "school", "ticking", "tasks", "facts", "unread", "budget"} <= set(state)
        assert state["ticking"] is True, "Monday 18:00 is inside the window"

        seq = rt.transcript.append("marker")
        rt.transcript.append("message_out", text="later")
        newer = c.get(f"/v2/messages?since={seq}", headers=auth).get_json()["messages"]
        assert [m["text"] for m in newer] == ["later"], newer

        # Ordinary bad input is a 400, never a 500. Each of these crashed.
        assert c.post("/v1/ios/override", json={"minutes": "abc"},
                      headers=auth).status_code == 400
        assert c.post("/v1/ios/override", json={"minutes": [1]},
                      headers=auth).status_code == 400
        assert c.post("/v1/ios/override", json={"minutes": 99999},
                      headers=auth).status_code == 400
        assert c.post("/v1/tasks", json={"title": 123}, headers=auth).status_code == 400
        assert c.post("/v1/planner", json={"tasks": "oops"}, headers=auth).status_code == 400
        assert c.post("/v1/planner", json={"tasks": [1, None]},
                      headers=auth).get_json()["added"] == []
        assert c.post("/v2/say", json={"text": 42}, headers=auth).status_code == 400
        assert c.post("/v1/chat", json=["not", "a", "dict"], headers=auth).status_code == 400

        # A non-ASCII Authorization header must deny, not crash.
        assert c.get("/v1/tasks",
                     headers={"Authorization": "Bearer ünicode"}).status_code == 401

        # One huge paste must not enter the two-day window.
        assert c.post("/v2/say", json={"text": "x" * 300_000},
                      headers=auth).status_code == 413

        assert c.get("/v1/ios/mode", headers=auth).status_code == 200
        assert c.post("/v1/ios/override", json={"minutes": 30},
                      headers=auth).get_json()["minutes"] == 30
        assert c.get("/v1/planner", headers=auth).status_code == 200
        assert c.get("/v1/ac", headers=auth).status_code == 200

        clock.set_for_test(None)
        del os.environ["ARGON_HOME"]
    print("api selftest ok")


if __name__ == "__main__":
    _selftest()
