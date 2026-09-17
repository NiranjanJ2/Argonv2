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

import json
import os
import threading
import time
from datetime import datetime, timedelta
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

#: How often his Classroom work is pulled onto the board. A dozen Google calls,
#: and homework does not change every five minutes.
CLASSROOM_SYNC_MINUTES = 30

#: How many tasks the prompt tail carries. More than this and the tail says so
#: rather than truncating silently.
BOARD_LIMIT = 25

#: How long the agenda is reused before the calendar is asked again.
AGENDA_TTL_MINUTES = 10


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
        self._prompt_day = clock.day_key()
        self._last_classroom_sync: datetime | None = None
        self._agenda_cache: list[str] = []
        self._agenda_at: datetime | None = None
        self._posted_cache: list[str] = []
        self._posted_at: datetime | None = None
        self._channels: list = []
        self._stop = threading.Event()
        # One turn at a time. The Flask thread, the tick loop and the Discord
        # worker are three entry points into the same agent; without this they
        # overlap, double the spend on one moment, and interleave their rows.
        self._turn_lock = threading.Lock()
        self._register_tools()

    # -- live state -------------------------------------------------------
    def live_state(self) -> str:
        """The board and standing facts, for the tail of every prompt."""
        parts = []
        tasks = self.store.tasks()
        if tasks:
            # Split, because the board is sorted due-ascending and that puts the
            # stalest work first. On 16 Sep the brief opened with "two oldest
            # overdue are AP Narrative Rewrite and HW 12, both due 2026-09-03"
            # — thirteen days gone, nothing he was going to do that evening,
            # and it read as the assistant picking assignments at random.
            # Overdue is a standing condition; live work is the news.
            today = clock.day_key()
            live = [t for t in tasks if not t.due or t.due >= today]
            late = [t for t in tasks if t.due and t.due < today]

            shown = live[:BOARD_LIMIT]
            board = "\n".join(f"- [{t.id}] {t.line()}" for t in shown)
            if len(live) > len(shown):
                # Silent truncation against a prompt that demands "give him
                # all of it" guarantees a quietly incomplete answer, and he
                # cannot tell. At 59 open the model saw 25 and had no idea.
                board += (f"\n… and {len(live) - len(shown)} more not shown. "
                          f"Call list_tasks for the full board before telling "
                          f"him what is due.")
            parts.append(f"Live tasks ({len(live)}), soonest first:\n{board}"
                         if live else "Nothing due today or later.")
            if late:
                parts.append(
                    f"Also {len(late)} overdue, oldest {late[0].due}. This is a "
                    f"standing backlog, not news — do not open with it, and do "
                    f"not list it unless he asks. Call list_tasks for the full "
                    f"board.")
        else:
            parts.append("Open tasks: none.")
        facts = self.store.facts()
        if facts:
            parts.append("What you know:\n" + "\n".join(f"- {f}" for f in facts[:25]))
        if (agenda := self._agenda()):
            parts.append(agenda)
        if (posted := self._posted()):
            parts.append(posted)
        period = bell.current_period()
        if period:
            parts.append(f"He is in {period} right now.")
        if (quiet := self.store.quiet()) is not None:
            until, reason = quiet
            parts.append(
                f"YOU ARE STANDING DOWN until {until:%a %H:%M} because: {reason}. "
                f"You have already told him. Do not tell him again, and do not "
                f"send anything unless he speaks first or something genuinely new "
                f"and urgent has happened.")
        return "\n\n".join(parts)

    def _refresh_prompt(self) -> None:
        """Rebuild the system prompt when the day turns over.

        It carries the date and today's bell schedule, and the service runs for
        weeks at a time — without this a Friday daemon still tells the model it
        is the Monday it booted on.
        """
        today = clock.day_key()
        if today != self._prompt_day:
            self.agent.system = system_prompt()
            self._prompt_day = today

    def _agenda(self) -> str:
        """Today's remaining events, stated in the prompt.

        v1's lesson, and it is the one the build guide generalises: where a
        fact must be stated, fetch it and put it in the prompt rather than
        hoping for a tool call — the model reliably skips optional lookups.
        An event he booked at noon produced silence at 18:45, and the single
        most useful thing an assistant can say is "you have X in fifteen
        minutes".

        Cached, because this runs on every turn and the calendar does not
        change every five minutes.
        """
        now = clock.now()
        if (self._agenda_at is None
                or (now - self._agenda_at).total_seconds() > AGENDA_TTL_MINUTES * 60):
            account = google.account_for("calendar", self.cfg.google_accounts)
            try:
                events = google.todays_events(account) if account else []
            except Exception as e:  # noqa: BLE001 - a bad read must not stop the turn
                self.transcript.append("agenda_failed", summary=repr(e)[:200])
                events = []
            self._agenda_cache, self._agenda_at = ([
                f"- {e['at']:%H:%M} {e['title']}"
                + (f"  ← in {e['minutes']} minutes" if 0 <= e["minutes"] <= 30 else "")
                for e in events], now)
        if not self._agenda_cache:
            return ""
        return "Left on his calendar today:\n" + "\n".join(self._agenda_cache)

    def _posted(self) -> str:
        """What his teachers posted that is not an assignment.

        Same lesson as _agenda: state it, do not hope for a tool call. A class
        whose work arrives as a posted material contributes nothing to the task
        board, so without this the model says "nothing due for AP Lang" with
        total confidence and he believes it.

        Labelled "not tracked as tasks" on purpose. These have no submission
        state, so nothing can ever mark them done — if the model treats them as
        board items it will nag about the same reading for weeks.
        """
        now = clock.now()
        if (self._posted_at is None
                or (now - self._posted_at).total_seconds() > AGENDA_TTL_MINUTES * 60):
            account = google.account_for("classroom", self.cfg.google_accounts)
            try:
                items = google.recent_materials(account) if account else []
            except Exception as e:  # noqa: BLE001 - a bad read must not stop the turn
                self.transcript.append("materials_failed", summary=repr(e)[:200])
                items = []
            self._posted_cache, self._posted_at = ([
                f"- {m['at']:%a} {m['course']}: {m['title']}" for m in items], now)
        if not self._posted_cache:
            return ""
        return ("Recently posted by his teachers (not tracked as tasks, and "
                "nothing here can be marked done):\n"
                + "\n".join(self._posted_cache))

    def turn(self, *, background: bool) -> object:
        """One turn with live state attached. Every entry point — tick, chat,
        webhook — goes through here, so they all get the same picture and they
        queue rather than overlap."""
        with self._turn_lock:
            self._refresh_prompt()
            return self.agent.turn(background=background, extra=self.live_state())

    def receive(self, text: str, *, source: str = "ios") -> object:
        self.transcript.append("message_in", text=text, source=source)
        return self.turn(background=False)

    # -- delivery ---------------------------------------------------------
    def deliver(self, text: str) -> list[str]:
        """Send to every attached channel.  Returns the failures.

        An empty list means at least one channel accepted it. A channel that
        fails must not stop the others, and must not raise into the agent loop —
        but the caller has to be told, or `say` reports success for a message
        that reached nobody.
        """
        if not self._channels:
            # No channel at all is a total delivery failure, not a quiet
            # success. Returning [] here let `say` record a message_out for
            # something nobody could possibly have received.
            self.transcript.append("delivery_failed", summary="no channels attached")
            return ["no channel is attached"]

        errors: list[str] = []
        for channel in self._channels:
            name = getattr(channel, "__self__", channel)
            name = getattr(name, "name", getattr(channel, "__name__", "channel"))
            try:
                channel(text)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
        if not errors:
            return []
        if len(errors) == len(self._channels):
            self.transcript.append("delivery_failed", summary="; ".join(errors)[:300])
            return errors
        # Some route worked, so he got it. Still worth recording — a channel
        # that is quietly broken should be findable before it is the only one.
        self.transcript.append("delivery_partial", summary="; ".join(errors)[:300])
        return []

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
        """Deliver to the phone. Raises when it could not.

        A dead token is reported, never acted on here: which environment the
        installed build uses decides whether the reason means anything, and
        this code does not know that. Returning quietly when APNs is off or no
        device is registered would report every message as delivered.
        """
        if not self.cfg.apns.enabled:
            raise RuntimeError("apns is disabled")
        token = self.device_token()
        if not token:
            raise RuntimeError("no device registered for push")
        result = self.push.alert(token, text)
        if not result.ok:
            self.transcript.append("push_failed",
                                   summary=f"{result.status} {result.reason}")
            raise RuntimeError(f"apns {result.status} {result.reason}")

    def sync_classroom(self) -> str:
        """Put his Classroom work on the task board.

        Without this the board was empty while he had twenty assignments
        outstanding — the agent could read them with a tool, but "what's due"
        on his phone said "Nothing open". His homework *is* his task board.

        Imports are keyed on the Classroom id, so running this repeatedly
        updates rather than duplicates, and work that is no longer outstanding
        (he handed it in, at school, on a laptop) is completed here.
        """
        account = google.account_for("classroom", self.cfg.google_accounts)
        if account is None:
            return "no Google account is authorised for classroom"
        try:
            items, refused = google.outstanding_assignments(account)
        except google.GoogleUnavailable as e:
            return str(e)
        except Exception as e:  # noqa: BLE001
            return f"Classroom sync failed: {type(e).__name__}: {e}"

        known = self.store.external_ids(source="classroom")
        seen: set[str] = set()
        added = 0
        for item in items:
            seen.add(item["id"])
            if item["id"] not in known:
                added += 1
            self.store.add_task(item["title"], subject=item["course"],
                                due=item["due"] or "", source="classroom",
                                external_id=item["id"])

        # Anything he no longer owes is done, however he did it — but only
        # when every course answered.
        #
        # Absence from a partial read is not evidence of completion. Four of
        # his Robotics courses return 403, and closing on that basis marked
        # their homework done permanently: `add_task` matches the completed row
        # and updates it, so the work never came back and the board said
        # "done". Losing work silently is the failure this guard exists to
        # prevent — v1 had the same rule and the same reason.
        closed = 0
        if refused:
            note_suffix = (f"; kept {len(known) - len(seen & known.keys())} open "
                           f"because {len(refused)} course(s) could not be read")
        else:
            note_suffix = ""
            for external_id, task in known.items():
                if external_id not in seen:
                    if self.store.complete_task(task.id):
                        closed += 1

        self._last_classroom_sync = clock.now()
        note = f"classroom: {added} added, {closed} closed, {len(items)} outstanding"
        note += note_suffix
        if refused:
            note += f" (could not read: {', '.join(refused)})"
        self.transcript.append("classroom_sync", summary=note)
        return note

    def remember_discord_channel(self, channel_id: str) -> None:
        """Write the channel he last used back to config, so a restart still
        knows where to deliver."""
        path = config.home() / "config.json"
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
            data.setdefault("discord", {})["channel_id"] = channel_id
            # Write-then-rename. This file holds the model key, the bot token
            # and the API bearer token; `write_text` truncates first, so a kill
            # mid-write would destroy all three.
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.chmod(0o600)
            os.replace(tmp, path)
        except (OSError, json.JSONDecodeError) as e:
            # A hand-edited config with a trailing comma used to raise straight
            # into the Discord worker.
            self.transcript.append("config_write_failed", summary=str(e))
            return
        # Keep the running process in step; it used to hold the old target
        # until the next restart.
        self.cfg.discord.channel_id = channel_id

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
                # Steering text is kept out of the reportable sentence. Argon
                # pasted "(say it once; nothing you can do will fix it
                # tonight)" — instructions addressed to itself — into a message
                # to him, and garbled "tell Niranjan to run X on the server"
                # into "tell the server".
                return (f"REPORT TO HIM: Classroom is unavailable — no Google "
                        f"account is authorised for {capability}.\n"
                        f"NOT FOR HIM: the fix is `argon google-auth <account>` "
                        f"run on the server by Niranjan. You cannot do it. Say it "
                        f"once and do not raise it again tonight.")
            return _google(fn, account, *args)

        t.add("calendar", "His calendar for the next few days.",
              lambda days=7: route("calendar", google.list_events, days),
              params={"days": {"type": "integer"}}, untrusted=True)
        t.add("add_event", "Put something on his calendar.",
              lambda title, start, end="":
                  route("calendar", google.create_event, title, start, end),
              params={"title": {"type": "string"},
                      "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM"},
                      "end": {"type": "string"}},
              required=["title", "start"])
        t.add("assignments", "Outstanding Google Classroom work.",
              lambda: route("classroom", google.list_assignments), untrusted=True)
        t.add("search_mail",
              "Search his mail. Searches every authorised account, including "
              "school — teachers and counsellors write there.",
              lambda query: _google_all(google.search_all_mail,
                                        self.cfg.google_accounts, query),
              params={"query": {"type": "string"}}, required=["query"],
              untrusted=True)

        def stand_down(hours: float = 12, reason: str = "") -> str:
            if not 0.25 <= hours <= 48:
                return "Error: hours must be between 0.25 and 48."
            until = clock.now() + timedelta(hours=float(hours))
            store.set_quiet(until, reason or "he asked for quiet")
            # Going quiet is the action. Announcing it is not going quiet — on
            # 14 Sep it sent "Standing down for the night" at 23:23 and again
            # at 23:54. say() enforces this on unprompted turns; the old return
            # value said "say it once now", which is what it kept doing.
            self.agent._went_quiet = True
            return (f"Standing down until {until:%a %H:%M}. You will not be woken "
                    f"before then. This is recorded — do not message him about it.")

        def resume() -> str:
            """Come back on watch. Only he can lift a stand-down.

            The model stood itself down and un-stood itself twenty minutes
            later with no message from him in between, then sent the two worst
            messages in the log. A commitment the committer can silently
            cancel is not a commitment.
            """
            quiet = store.quiet()
            if quiet is None:
                return "not stood down"
            spoke_at = self.transcript.last("stood_down")
            since = self.transcript.since(spoke_at.seq) if spoke_at else []
            if not any(e.kind == "message_in" for e in since):
                return ("Refused: he has not said anything since you stood down. "
                        "Only he lifts it. It expires on its own at "
                        f"{quiet[0]:%a %H:%M}.")
            store.clear_quiet()
            return "back on watch"

        t.add("stand_down",
              "Go quiet. Call this when he asks you to back off, or when there is "
              "nothing you can do until something outside changes. This is the "
              "whole action: it is recorded and survives restarts, so there is "
              "nothing to announce and no need to call it again.",
              stand_down,
              params={"hours": {"type": "number", "description": "how long to stay quiet"},
                      "reason": {"type": "string"}})
        t.add("resume", "Come back on watch before the stand-down expires.", resume)

        t.add("sync_classroom",
              "Pull his Classroom work onto the task board. Runs on its own "
              "every half hour; call it only if he says something is missing.",
              self.sync_classroom)
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
    def tick_once(self, *, force: bool = False) -> object | None:
        """One scheduled look.  Returns None when the clock says don't bother."""
        if not force and not schedule.should_tick():
            return None
        # Keep the board current before deciding anything from it. Half-hourly,
        # because it is a dozen Google calls and his homework does not change
        # every five minutes.
        if force or self._last_classroom_sync is None or (
                clock.now() - self._last_classroom_sync
        ).total_seconds() > CLASSROOM_SYNC_MINUTES * 60:
            try:
                self.sync_classroom()
            except Exception as e:  # noqa: BLE001 - a bad sync must not stop the tick
                self.transcript.append("classroom_sync_failed", summary=repr(e))

        # A commitment to silence is honoured, not re-litigated. This is not a
        # gate on judgement: the model made this decision and Python is only
        # remembering it. He can still reach Argon at any time — an inbound
        # message goes through `receive`, which never consults this.
        if not force and self.store.quiet() is not None:
            return None
        # Honour the cadence across restarts. `run()` ticks on entry, so three
        # redeploys in a minute produced three looks seventeen seconds apart and
        # two near-identical messages. The cadence is the scheduler's job — cost
        # and rhythm — and a restart is not a reason to break it.
        if not force and (last := self.transcript.last("tick")) is not None:
            since = (clock.now() - datetime.fromisoformat(last.at)).total_seconds()
            if since < schedule.TICK_MINUTES * 60:
                return None
        if self._announce_budget_stop():
            return None
        self.transcript.append("tick")
        return self.turn(background=True)

    def _announce_budget_stop(self) -> bool:
        """Tell him the budget is gone, if it is. Returns True when capped.

        Sent directly rather than through the model — there is no budget left
        to ask it to phrase this. It is also the only send that bypasses
        `Agent.say`, so it has to check delivery itself: recording a
        `message_out` blind, after `take_notification` has already burnt its
        once-a-month flag, means he is never told at all.
        """
        notice = budget.take_notification(self.cfg.monthly_cap_usd)
        if not notice:
            return budget.month()["usd"] >= self.cfg.monthly_cap_usd
        errors = self.deliver(notice)
        if errors:
            # Put the flag back so the next tick tries again.
            budget.rearm_notification()
            self.transcript.append("undelivered", text=notice,
                                   summary="; ".join(errors)[:200])
        else:
            self.transcript.append("message_out", text=notice)
        return True

    def run(self) -> None:
        """Tick until stopped, on a fixed period rather than a fixed pause.

        Waiting the full interval *after* the work makes the real period
        `work + interval`: a turn plus a Classroom sync took nearly three
        minutes and ticks drifted to almost eight apart. That matters beyond
        tidiness — the prompt cache expires after a few idle minutes, so drift
        turns warm ticks into full-price ones.
        """
        period = schedule.TICK_MINUTES * 60
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.tick_once()
            except Exception as e:  # noqa: BLE001 - a bad tick must not end the loop
                self.transcript.append("tick_failed", summary=repr(e))
            elapsed = time.monotonic() - started
            # Never busy-loop if a turn somehow outruns the interval.
            self._stop.wait(max(30.0, period - elapsed))

    def stop(self) -> None:
        self._stop.set()


def _google_all(fn, accounts: list[str], *args) -> str:
    """Like `_google`, for a call that spans every authorised account."""
    try:
        return fn(accounts, *args)
    except google.GoogleUnavailable as e:
        return str(e)
    except Exception as e:  # noqa: BLE001
        return f"Google call failed: {type(e).__name__}: {e}"


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
    from datetime import datetime, timedelta, timedelta

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
        assert rt._agenda_at is not None, "the agenda is fetched, not left to a tool"
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

        # A second look moments later is the restart bug: skip it.
        assert rt.tick_once() is None, "cadence must survive a restart"
        clock.set_for_test(datetime(2026, 9, 14, 18, 0, tzinfo=clock.TZ)
                           + timedelta(minutes=schedule.TICK_MINUTES + 1))
        # insert, not append: the queue pops from the front and "hello" below
        # is reserved for the interactive turn.
        scripted.insert(0, provider.Reply(text="still quiet"))
        assert rt.tick_once() is not None, "and resume once the interval has passed"
        clock.set_for_test(datetime(2026, 9, 14, 18, 0, tzinfo=clock.TZ))
        msgs = context.build(rt.transcript, "S", extra=rt.live_state())
        assert "AP Chem pset" in msgs[-1]["content"], "board must reach the model"

        out = rt.receive("hey")
        assert out.spoke is True and sent == ["hello"]
        # A stand-down suppresses the proactive tick but never a reply to him.
        assert "Standing down until" in rt.tools.call(
            "stand_down", {"hours": 3, "reason": "he asked"}, background=True)
        assert rt.tick_once() is None, "no ticking while stood down"
        assert "STANDING DOWN" in rt.live_state()

        # Only he lifts a stand-down; the model cannot cancel its own. Checked
        # before he says anything, which is the case that matters.
        assert rt.tools.call("resume", {}, background=True).startswith("Refused")
        assert rt.store.quiet() is not None, "the commitment must hold"

        scripted.insert(0, provider.Reply(text="still here"))
        assert rt.receive("you up?").spoke is True, "he can always reach it"
        # Now that he has spoken, it may come back on watch.
        assert rt.tools.call("resume", {}, background=True) == "back on watch"
        assert rt.store.quiet() is None
        assert rt.tools.call("stand_down", {"hours": 999}, background=True).startswith("Error")

        # Outside the window a tick costs nothing at all.
        clock.set_for_test(datetime(2026, 9, 19, 20, 0, tzinfo=clock.TZ))  # Saturday
        assert rt.tick_once() is None

        assert rt.tools.call("complete_task", {"task": "nonsense"},
                             background=False).startswith("No open task")
        assert "completed AP Chem pset" == rt.tools.call(
            "complete_task", {"task": "chem"}, background=False)
        assert rt.tools.call("update_task", {"task": "nope"},
                             background=True).startswith("No task matching")

        # Order-independent: earlier checks may have produced replies too.
        assert rt.unread() >= 1, "replies above are unread"
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
        # The reportable half is separated from the steering half, so the
        # model cannot paste its own instructions into a message to him.
        unavailable = rt.tools.call("calendar", {}, background=True)
        assert "REPORT TO HIM:" in unavailable and "NOT FOR HIM:" in unavailable
        assert "no Google account is authorised for calendar" in unavailable
        assert "no Google account is authorised" in rt.sync_classroom()

        # A channel that throws does not break delivery to the others.
        rt.add_channel(lambda _: (_ for _ in ()).throw(RuntimeError("discord down")))
        rt.add_channel(sent.append)
        assert rt.deliver("x") == [], "one good channel means he received it"
        assert sent.count("x") == 2, sent
        kinds = [e.kind for e in rt.transcript.window(3)]
        assert "delivery_partial" in kinds, "a broken channel stays findable"
        assert "delivery_failed" not in kinds

        # When nothing works, say() must hear about it.
        dead = Runtime(config.Config())
        dead.add_channel(lambda _: (_ for _ in ()).throw(RuntimeError("no channel")))
        errors = dead.deliver("y")
        assert errors and "no channel" in errors[0]
        assert "delivery_failed" in [e.kind for e in dead.transcript.window(3)]

        clock.set_for_test(None)
        del os.environ["ARGON_HOME"]
    print("runtime selftest ok")


if __name__ == "__main__":
    _selftest()
