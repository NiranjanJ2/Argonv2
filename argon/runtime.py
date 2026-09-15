"""The object graph and the tick loop.

Everything is wired here and nowhere else, so the whole system can be read in
one file: what exists, what it is given, and what wakes it.

The tick loop is deliberately dull.  It asks ``schedule.should_tick`` whether
this is a moment worth spending money on, appends a ``tick`` event, and runs a
turn.  It decides nothing else.  Every judgement — whether anything is worth
saying, whether he has been asked already, whether silence is right — belongs to
the model, which is the entire point of the rewrite.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from argon import bell, budget, clock, config, context, schedule
from argon.agent import Agent
from argon.integrations import google
from argon.integrations.ac import ACError, Gree
from argon.integrations.push import Push, PushError
from argon.store import Store
from argon.tools import Tools
from argon.transcript import Transcript

PROMPT = Path(__file__).parent / "prompts" / "argon.md"


def system_prompt() -> str:
    """Static for the whole day, so it caches.

    An operator override at ``~/.argon2/SOUL.md`` replaces the shipped prompt
    entirely — editing behaviour should not need a deploy.
    """
    override = config.home() / "SOUL.md"
    base = override.read_text() if override.exists() else PROMPT.read_text()
    today = clock.now()
    return f"{base}\n\n---\n\nToday is {today.strftime('%A %d %B %Y')}. {bell.describe()}"


class Runtime:
    def __init__(self, cfg: config.Config | None = None) -> None:
        self.cfg = cfg or config.load()
        self.transcript = Transcript(config.path("transcript.db"))
        self.store = Store(config.path("store.db"), self.transcript)
        self.tools = Tools(self.transcript)
        self.push = Push(self.cfg.apns)
        self.ac = Gree()
        self.agent = Agent(self.cfg, self.transcript, self.store, self.tools,
                           system_prompt())
        self.agent.deliver = self.deliver
        self._channels: list = []
        self._stop = threading.Event()
        self._register_tools()

    # -- live state -------------------------------------------------------
    def live_state(self) -> str:
        """The board and standing facts, for the tail of every prompt."""
        parts = []
        tasks = self.store.tasks()
        if tasks:
            parts.append("Open tasks:\n" + "\n".join(
                f"- [{t.id}] {t.line()}" for t in tasks[:25]))
        else:
            parts.append("Open tasks: none.")
        facts = self.store.facts()
        if facts:
            parts.append("What you know:\n" + "\n".join(f"- {f}" for f in facts[:25]))
        period = bell.current_period()
        if period:
            parts.append(f"He is in {period} right now.")
        return "\n\n".join(parts)

    def turn(self, *, background: bool) -> object:
        """One turn with live state attached.  Wraps Agent.turn so every entry
        point — tick, chat, webhook — gets the same picture."""
        original = context.build

        def with_state(t, system, **kw):
            kw["extra"] = self.live_state()
            return original(t, system, **kw)

        context.build = with_state  # type: ignore[assignment]
        try:
            return self.agent.turn(background=background)
        finally:
            context.build = original  # type: ignore[assignment]

    def receive(self, text: str, *, source: str = "ios") -> object:
        self.transcript.append("message_in", text=text, source=source)
        return self.turn(background=False)

    # -- delivery ---------------------------------------------------------
    def deliver(self, text: str) -> None:
        """Send to every attached channel.  A channel that fails must not stop
        the others, and must not raise into the agent loop."""
        for channel in self._channels:
            try:
                channel(text)
            except Exception as e:  # noqa: BLE001
                self.transcript.append("delivery_failed",
                                       summary=f"{getattr(channel, 'name', channel)}: {e}")

    def add_channel(self, send) -> None:
        self._channels.append(send)

    # -- client state -----------------------------------------------------
    def unread(self) -> int:
        """Messages sent since he last opened the app.

        Derived from the transcript rather than kept as a counter, so it cannot
        drift out of step with what was actually sent.
        """
        seen = self.transcript.last("read")
        rows = (self.transcript.since(seen.seq) if seen
                else self.transcript.window(7))
        return sum(1 for e in rows if e.kind == "message_out")

    def mark_read(self) -> None:
        self.transcript.append("read")

    def register_device(self, token: str) -> None:
        """Remember his push token and start pushing to it."""
        config.path("device_token").write_text(token)
        self.transcript.append("device_registered", summary=token[:8] + "…")

    def device_token(self) -> str:
        p = config.home() / "device_token"
        return p.read_text().strip() if p.exists() else ""

    def push_channel(self, text: str) -> None:
        """Deliver to the phone. A dead token is recorded, never acted on here:
        which environment the installed build uses decides whether the reason
        means anything, and this code does not know that."""
        token = self.device_token()
        if not token or not self.cfg.apns.enabled:
            return
        result = self.push.alert(token, text)
        if not result.ok:
            self.transcript.append("push_failed",
                                   summary=f"{result.status} {result.reason}")

    def ac_units(self) -> list[dict]:
        return [u.as_dict() for u in self.ac.units.values()]

    def ac_set(self, mac: str, changes: dict) -> dict:
        try:
            return {"ok": True, "result": self.ac.set(mac, **changes)}
        except ACError as e:
            return {"ok": False, "error": str(e)}

    # -- tools ------------------------------------------------------------
    def _register_tools(self) -> None:
        t, store = self.tools, self.store

        t.add("say", "Send Niranjan a message. This is the only way to reach him.",
              self.agent.say,
              params={"text": {"type": "string", "description": "exactly what he receives"}},
              required=["text"])

        t.add("list_tasks", "The open task board with ids.",
              lambda: "\n".join(f"[{x.id}] {x.line()}" for x in store.tasks())
                      or "Nothing open.")
        t.add("add_task", "Add a task he has committed to.",
              lambda title, subject="", due="", priority="normal":
                  f"added [{store.add_task(title, subject=subject, due=due, priority=priority).id}]",
              params={"title": {"type": "string"},
                      "subject": {"type": "string"},
                      "due": {"type": "string", "description": "YYYY-MM-DD"},
                      "priority": {"type": "string", "enum": ["low", "normal", "high"]}},
              required=["title"])
        def complete_task(task: str) -> str:
            found = store.find_task(task)
            if found is None:
                return f"No open task matching {task!r}."
            done = store.complete_task(found.id)
            return f"completed {done.title}" if done else f"{found.title} was already done"

        def update_task(task: str, **changes: str) -> str:
            found = store.find_task(task)
            if found is None:
                return f"No task matching {task!r}."
            updated = store.update_task(found.id, **changes)
            return f"updated {updated.title}" if updated else "nothing to change"

        t.add("complete_task", "Mark a task done. Only when he says it is done.",
              complete_task,
              params={"task": {"type": "string", "description": "id or part of the title"}},
              required=["task"], background=False)
        t.add("update_task", "Change a task's title, subject, due date or priority.",
              update_task,
              params={"task": {"type": "string"}, "title": {"type": "string"},
                      "subject": {"type": "string"}, "due": {"type": "string"},
                      "priority": {"type": "string"}},
              required=["task"])

        t.add("remember", "Store an operational fact that will matter after today.",
              lambda text, standing=False, until="":
                  f"remembered ({store.remember(text, standing=standing, until=until)})",
              params={"text": {"type": "string"},
                      "standing": {"type": "boolean",
                                   "description": "true for the recurring shape of his weeks"},
                      "until": {"type": "string", "description": "YYYY-MM-DD, when it stops mattering"}},
              required=["text"])
        t.add("note", "Write a line to today's journal. Not durable.",
              lambda text: (self.transcript.append("note", summary=text), "noted")[1],
              params={"text": {"type": "string"}}, required=["text"])

        # Google accounts are role-specialised — work holds calendar and tasks,
        # school holds Classroom — so each tool resolves the account that can
        # actually serve it. The model never has to know which is which, and
        # cannot misroute a call into an "insufficient scopes" error.
        def route(capability: str, fn, *args) -> str:
            account = google.account_for(capability, self.cfg.google_accounts)
            if account is None:
                return (f"No Google account is authorised for {capability}. "
                        f"Run `argon google-auth <account>` on the server.")
            return _google(fn, account, *args)

        t.add("calendar", "His calendar for the next few days.",
              lambda days=7: route("calendar", google.list_events, days),
              params={"days": {"type": "integer"}})
        t.add("add_event", "Put something on his calendar.",
              lambda title, start, end="":
                  route("calendar", google.create_event, title, start, end),
              params={"title": {"type": "string"},
                      "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM"},
                      "end": {"type": "string"}},
              required=["title", "start"])
        t.add("assignments", "Outstanding Google Classroom work.",
              lambda: route("classroom", google.list_assignments))
        t.add("search_mail", "Search his mail.",
              lambda query: route("gmail", google.search_mail, query),
              params={"query": {"type": "string"}}, required=["query"])

        t.add("schedule_today", "Today's bell schedule.", lambda: bell.describe())
        t.add("set_ac", "Change the air conditioner.",
              lambda mac="", power=None, temp=None, mode=None: str(self.ac_set(
                  mac or next(iter(self.ac.units), ""),
                  {k: v for k, v in (("power", power), ("temp", temp), ("mode", mode))
                   if v is not None})),
              params={"mac": {"type": "string"}, "power": {"type": "integer"},
                      "temp": {"type": "integer"}, "mode": {"type": "string"}})
        t.add("spend", "What Argon has spent this month.",
              lambda: (lambda m: f"${m['usd']:.2f} of ${self.cfg.monthly_cap_usd:.2f} "
                                 f"over {m['calls']} calls; "
                                 f"{budget.cached_fraction():.0%} of prompt tokens cached")(
                  budget.month()))

    # -- loops ------------------------------------------------------------
    def tick_once(self) -> object | None:
        """One scheduled look.  Returns None when the clock says don't bother."""
        if not schedule.should_tick():
            return None
        notice = budget.take_notification(self.cfg.monthly_cap_usd)
        if notice:
            # Sent directly: there is no budget left to ask the model to phrase it.
            self.transcript.append("message_out", text=notice)
            self.deliver(notice)
            return None
        self.transcript.append("tick")
        return self.turn(background=True)

    def run(self) -> None:
        """Tick until stopped."""
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as e:  # noqa: BLE001 - a bad tick must not end the loop
                self.transcript.append("tick_failed", summary=repr(e))
            self._stop.wait(schedule.TICK_MINUTES * 60)

    def stop(self) -> None:
        self._stop.set()


def _google(fn, account: str, *args) -> str:
    """Google failures are answers, not exceptions — the model relays them."""
    try:
        return fn(account, *args)
    except google.GoogleUnavailable as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"Google call failed: {type(e).__name__}: {e}"


def _selftest() -> None:
    import os
    import tempfile
    from datetime import datetime

    from argon import provider

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ARGON_HOME"] = tmp
        clock.set_for_test(datetime(2026, 9, 14, 18, 0, tzinfo=clock.TZ))  # Monday
        rt = Runtime(config.Config())

        sent: list[str] = []
        rt.add_channel(sent.append)

        names = rt.tools.names()
        for expected in ("say", "list_tasks", "add_task", "remember", "calendar", "spend"):
            assert expected in names, names
        # Completing work is something he says, never a tick.
        assert "complete_task" not in [s["function"]["name"]
                                       for s in rt.tools.schemas(background=True)]

        assert "Open tasks: none." in rt.live_state()
        rt.store.add_task("AP Chem pset", due="2026-09-16")
        rt.store.remember("practice Tuesdays", standing=True)
        state = rt.live_state()
        assert "AP Chem pset" in state and "practice Tuesdays" in state

        # Live state rides in the tail, never the cached system prompt.
        assert "AP Chem" not in system_prompt()
        assert "Whitney" not in rt.live_state()

        scripted = [provider.Reply(text="quiet"), provider.Reply(text="hello")]
        provider.complete = lambda *a, **k: scripted.pop(0)  # type: ignore[assignment]

        out = rt.tick_once()
        assert out is not None and out.spoke is False and sent == []
        msgs = context.build(rt.transcript, "S", extra=rt.live_state())
        assert "AP Chem pset" in msgs[-1]["content"], "board must reach the model"

        out = rt.receive("hey")
        assert out.spoke is True and sent == ["hello"]

        # Outside the window a tick costs nothing at all.
        clock.set_for_test(datetime(2026, 9, 19, 20, 0, tzinfo=clock.TZ))  # Saturday
        assert rt.tick_once() is None

        assert rt.tools.call("complete_task", {"task": "nonsense"},
                             background=False).startswith("No open task")
        assert "completed AP Chem pset" == rt.tools.call(
            "complete_task", {"task": "chem"}, background=False)
        assert rt.tools.call("update_task", {"task": "nope"},
                             background=True).startswith("No task matching")

        assert rt.unread() == 1, "the reply above is unread"
        rt.mark_read()
        assert rt.unread() == 0
        rt.transcript.append("message_out", text="one")
        rt.transcript.append("message_out", text="two")
        assert rt.unread() == 2
        rt.mark_read()
        assert rt.unread() == 0, "reading clears it"
        rt.transcript.append("message_out", text="three")
        assert rt.unread() == 1

        rt.register_device("d" * 64)
        assert rt.device_token() == "d" * 64
        assert rt.ac_units() == []
        assert rt.ac_set("nope", {"power": 1})["ok"] is False

        # A dead Google account answers instead of vanishing, and an
        # unauthorised capability says which command fixes it.
        assert "not connected" in _google(google.list_events, "nobody", 7)
        assert "No Google account is authorised for calendar" in \
            rt.tools.call("calendar", {}, background=True)

        # A channel that throws does not break delivery to the others.
        rt.add_channel(lambda _: (_ for _ in ()).throw(RuntimeError("discord down")))
        rt.add_channel(sent.append)
        rt.deliver("x")
        assert sent.count("x") == 2, sent
        assert "delivery_failed" in [e.kind for e in rt.transcript.window(3)]

        clock.set_for_test(None)
        del os.environ["ARGON_HOME"]
    print("runtime selftest ok")


if __name__ == "__main__":
    _selftest()
