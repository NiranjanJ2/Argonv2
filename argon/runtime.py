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

#: How close a calendar event has to be before it may interrupt. Long enough
#: that he can still act on it — a warning that arrives as the thing starts is
#: not a warning — and short enough that it is not just the day's agenda.
EVENT_URGENT_MINUTES = 30

#: How long a lock raised by starting a task may last. He presses done long
#: before this; the cap is only so a task started and forgotten cannot shut his
#: phone overnight.
TASK_LOCK_HOURS = 4

#: Renew a task's block when it is this close to lapsing, so a long session
#: never has the shield drop out from under it mid-work.
RENEW_WITHIN_MINUTES = 30


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
        self._load_ac()
        self.agent = Agent(self.cfg, self.transcript, self.store, self.tools,
                           system_prompt())
        self.agent.deliver = self.deliver
        self.agent.urgent_now = self.urgent_now
        self._prompt_day = clock.day_key()
        self._last_classroom_sync: datetime | None = None
        self._agenda_cache: list[str] = []
        self._agenda_events: list[dict] = []
        self._agenda_at: datetime | None = None
        #: What the phone was last told about the lock, so a change driven by
        #: the clock alone still reaches it.
        self._lock_was_live = False
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
        if (lock_line := self._lock_truth()):
            parts.append(lock_line)
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
            self._agenda_events = events
            self._agenda_cache, self._agenda_at = ([
                f"- {e['at']:%H:%M} {e['title']}"
                + (f"  ← in {e['minutes']} minutes"
                   if 0 <= e["minutes"] <= EVENT_URGENT_MINUTES else "")
                for e in events], now)
        if not self._agenda_cache:
            return ""
        return "Left on his calendar today:\n" + "\n".join(self._agenda_cache)

    def urgent_now(self) -> str | None:
        """Why the next few minutes are time-critical, or None.

        This is what buys an unprompted message the right to skip the backoff.
        It reads the clock rather than taking the model's word, because a tier
        the model declares is a tier it declares every time.

        Only the calendar qualifies. Tasks carry a due *date*, not a time, so
        "due today" says nothing about whether the next thirty minutes matter —
        treating a dated task as urgent would reopen the channel all evening
        and hand back the nudge loop through the side door.
        """
        self._agenda()   # refreshes _agenda_events under the same 10-minute TTL
        for e in self._agenda_events:
            if 0 <= e["minutes"] <= EVENT_URGENT_MINUTES:
                return f"{e['title']} starts in {e['minutes']} minutes"
        return None

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

    def turn(self, *, background: bool, extra: str = "") -> object:
        """One turn with live state attached. Every entry point — tick, chat,
        webhook — goes through here, so they all get the same picture and they
        queue rather than overlap.

        *extra* is appended after the live state, for the one caller that has
        something to say about this particular turn rather than about the world.
        """
        with self._turn_lock:
            self._refresh_prompt()
            state = self.live_state()
            if extra:
                state = f"{state}\n\n{extra}"
            return self.agent.turn(background=background, extra=state)

    def receive(self, text: str, *, source: str = "ios") -> object:
        self.transcript.append("message_in", text=text, source=source)
        self.sync_if_stale()
        return self.turn(background=False)

    def sync_if_stale(self) -> None:
        """Refresh Classroom before answering him, if it has been a while.

        Syncing used to happen only inside tick_once, which is gated to the
        evening window and skips Friday night, Saturday and Sunday morning. So
        the board froze from Friday evening until Sunday afternoon, and on
        Sunday morning he was told what was due using a board last written on
        Wednesday night — "Key Terms Chapter 7" had been assigned in between
        and did not exist as far as Argon was concerned.

        He can ask at any hour; the board has to be current when he does. The
        scheduler decides when Argon *speaks*, never how fresh the facts are.
        """
        last = self._last_classroom_sync
        if last is not None and (
                clock.now() - last).total_seconds() <= CLASSROOM_SYNC_MINUTES * 60:
            return
        try:
            self.sync_classroom()
        except Exception as e:  # noqa: BLE001 - a bad sync must not eat his message
            self.transcript.append("classroom_sync_failed", summary=repr(e)[:200])

    def receive_async(self, text: str, *, source: str = "ios") -> int:
        """Record his message, answer on a worker, return the transcript seq.

        The chat used to hold the HTTP request open for the whole turn — model
        latency, tool calls and all — so the phone showed "sending…" for ten
        seconds before the bubble even landed, then waited again for the reply.
        The send is now an append, which is instant; the answer arrives the same
        way any other message does.

        The seq comes back so the client knows what to wait past, rather than
        guessing from a count that a concurrent tick could also move.
        """
        seq = self.transcript.append("message_in", text=text, source=source)

        def answer() -> None:
            self.sync_if_stale()
            try:
                self.turn(background=False)
            except Exception as e:  # noqa: BLE001 - a worker must not die silently
                self.transcript.append("turn_failed", summary=repr(e)[:200])
            else:
                # The reply is in the transcript; tell the phone to come and get
                # it rather than making it poll on a timer it has to pay for.
                self.wake_phone("reply")

        threading.Thread(target=answer, name="argon-turn", daemon=True).start()
        return seq

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

        # First channel that accepts it wins; the rest are fallbacks, not
        # copies. Fanning out to every channel meant one message arrived twice
        # — once as a phone notification and once on Discord — so reading it in
        # one place left it unread in the other and the same sentence had to be
        # dismissed twice. Channels are attached in preference order.
        errors: list[str] = []
        for channel in self._channels:
            name = getattr(channel, "__self__", channel)
            name = getattr(name, "name", getattr(channel, "__name__", "channel"))
            try:
                channel(text)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
                continue
            if errors:
                # It landed, but only after something failed. Worth recording:
                # a channel that is quietly broken should be findable before it
                # is the only one left.
                self.transcript.append("delivery_fellback",
                                       summary=f"used {name} after: "
                                               + "; ".join(errors)[:260])
            return []
        self.transcript.append("delivery_failed", summary="; ".join(errors)[:300])
        return errors

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

    def begin_task(self, tid: str) -> object | None:
        """He started this: mark it, and put the shield up.

        Starting work and blocking distractions were two separate actions, so
        in practice the second one never happened — the block was a thing he
        had to remember to ask for at exactly the moment he had decided to stop
        thinking about his phone. One tap now does both.

        The lock is capped rather than open-ended. A task he starts and forgets
        must not lock him out overnight, and the cap is what makes "until I
        press done" safe to offer.
        """
        task = self.store.start_task(tid)
        if task is None:
            return None
        # Never over-write a lock he set himself. Starting a task used to
        # replace it with a task lock, which finishing that task then cleared —
        # so ticking something off silently ended a lock-in he had asked for.
        held = self.store.lock_record()
        if not (held and held.get("source") == "him"
                and held["from_at"] <= clock.now() < held["until_at"]):
            until = clock.now() + timedelta(hours=TASK_LOCK_HOURS)
            self.store.set_lock(until, f"working on {task.title}"[:120],
                                source="task", task_id=task.id)
        self.wake_phone("task started")
        return task

    def end_task(self, tid: str, *, done: bool) -> object | None:
        """Done with it, or putting it down. Either way the shield comes off."""
        current = self.store.task(tid)
        task = (self.store.complete_task(tid, by="him") if done
                else self.store.stop_task(tid))
        if task is None:
            return None
        if (done and current and current.source == "classroom"
                and current.external_id):
            self.store.set_disposition(current.external_id, "done")
        # Only lift a lock that this task raised. He may have asked for a
        # lock-in separately, and finishing one assignment is not a reason to
        # unblock his phone for the rest of the evening.
        rec = self.store.lock_record()
        if rec and rec.get("source") == "task" and rec.get("task_id") == task.id:
            self.store.clear_lock("the task it was raised for ended")
        self.wake_phone("task ended")
        return task

    def wake_phone(self, why: str) -> bool:
        """Push the phone to reconcile now, instead of waiting to be opened.

        Without this a published lock sits until he happens to launch the app
        or iOS grants a background refresh — which is the v1 failure in a new
        place: the state was right and nothing carried it to the device.

        Best-effort on purpose, and never raises. A lock whose push fails is
        still a lock; the phone applies it on its next wake either way, so a
        dead token must not take down the tool call that set it.

        The limit worth knowing: iOS stops delivering silent pushes *and*
        background refresh to an app the user has force-quit from the app
        switcher, until it is launched by hand again. Nothing server-side can
        reach a force-quit app.
        """
        if not self.cfg.apns.enabled:
            return False
        token = self.device_token()
        if not token:
            self.transcript.append("wake_skipped", summary="no device registered")
            return False
        try:
            result = self.push.silent(token, reason=why)
        except Exception as e:  # noqa: BLE001
            self.transcript.append("wake_failed", summary=repr(e)[:200])
            return False
        if not result.ok:
            self.transcript.append("wake_failed",
                                   summary=f"{result.status} {result.reason}")
        return result.ok

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
            items, outstanding_ids, refused = google.outstanding_assignments(account)
        except google.GoogleUnavailable as e:
            return str(e)
        except Exception as e:  # noqa: BLE001
            return f"Classroom sync failed: {type(e).__name__}: {e}"

        # His decisions outrank the import. v1 kept these in their own store so
        # a re-import could not resurrect work he had finished or dismissed —
        # "done" because plenty of coursework has nothing to submit, "ignored"
        # because some of it he is simply not doing.
        settled = {i["id"] for i in items
                   if self.store.disposition(i["id"]) is not None}
        if settled:
            items = [i for i in items if i["id"] not in settled]

        known = self.store.external_ids(source="classroom")
        seen: set[str] = set()
        added = 0
        for item in items:
            seen.add(item["id"])
            # Genuinely new, not merely "not currently open". external_ids only
            # lists open tasks, so counting against it reported every completed
            # assignment as freshly added on every sync — "20 added" for a board
            # where nothing had changed, which then went into the prompt.
            if self.store.task_by_external(item["id"]) is None:
                added += 1
            self.store.add_task(item["title"], subject=item["course"],
                                due=item["due"] or "", source="classroom",
                                external_id=item["id"],
                                due_at=item.get("due_at", ""))

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
                if external_id not in outstanding_ids:
                    # Marked as the sync's own closure, so a later sync that
                    # sees the work again may undo it. A closure he made by
                    # hand is never undone.
                    if self.store.complete_task(task.id, by="sync"):
                        closed += 1

        self._last_classroom_sync = clock.now()
        note = f"classroom: {added} added, {closed} closed, {len(items)} outstanding"
        note += note_suffix
        if refused:
            note += f" (could not read: {', '.join(refused)})"
        self.transcript.append("classroom_sync", summary=note)
        # Hand back the board it just produced, not only a count.
        #
        # The prompt's copy of the board is built once, at the start of the
        # turn. A sync called *during* the turn updates the store and changes
        # nothing the model can see, so on 20 Sep it synced, was told "5 added",
        # and then answered from the pre-sync board — reporting that APUSH had
        # nothing but two stale reminders while "Key Terms Chapter 7" sat in the
        # rows it had just written. The tool result is the only place fresh
        # state can reach it mid-turn, so the rows go here.
        board = "\n".join(f"- {t.line()}" for t in self.store.tasks()[:BOARD_LIMIT])
        if board:
            note += ("\n\nThe board now reads, and this supersedes the list "
                     "earlier in this conversation:\n" + board)
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

    def _load_ac(self) -> None:
        """Restore bound units from the store, and keep them saved thereafter.

        Binding is chatty and the key is stable, so it is remembered. Held only
        in memory, a restart forgot every unit and the next command failed
        until somebody re-ran a scan by hand.
        """
        from argon.integrations.ac import Unit
        for rec in self.store.ac_units():
            self.ac.units[rec["mac"]] = Unit(
                mac=rec["mac"], host=rec.get("host", ""), name=rec.get("name", ""),
                key=rec.get("key", ""), gcm=bool(rec.get("gcm", True)))
        self.ac.on_change = self._save_ac

    def _save_ac(self) -> None:
        self.store.save_ac_units([u.as_dict(with_key=True)
                                  for u in self.ac.units.values()])

    def ac_adopt(self, mac: str = "") -> dict:
        """Scan, bind whatever is found, and remember it.

        One call because the three steps have no useful intermediate state: a
        unit that is found but not bound cannot be commanded, and a bind that
        is not saved is lost on the next restart.
        """
        try:
            found = self.ac.scan()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"scan failed: {e}"}
        if not found:
            return {"ok": False, "error": "no air conditioners answered"}
        bound, failed = [], []
        for unit in found:
            if mac and unit.mac != mac:
                continue
            try:
                self.ac.bind(unit)
                bound.append(unit.as_dict())
            except ACError as e:
                failed.append(f"{unit.mac}: {e}")
        self._save_ac()
        return {"ok": bool(bound), "bound": bound, "failed": failed}

    def ac_units(self) -> list[dict]:
        return [u.as_dict() for u in self.ac.units.values()]

    def ac_find(self, wanted: str) -> str | None:
        """A unit by MAC, by name, or the only one there is.

        A Shortcut should not have to carry a MAC address. Names come from the
        unit itself and are what he actually calls them.
        """
        if not wanted:
            return next(iter(self.ac.units), None)
        if wanted in self.ac.units:
            return wanted
        needle = wanted.strip().lower()
        for mac, unit in self.ac.units.items():
            if (unit.name or "").strip().lower() == needle:
                return mac
        for mac, unit in self.ac.units.items():
            if needle in (unit.name or "").lower():
                return mac
        return None

    def ac_set(self, mac: str, changes: dict) -> dict:
        try:
            result = self.ac.set(mac, **changes)
            # set() may have relocated the unit; keep the new address.
            self._save_ac()
            return {"ok": True, "result": result}
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
        def settle(tid: str, state: str) -> None:
            """Record his decision against the Classroom item, not the row."""
            row = store.task(tid)
            if row is not None and row.source == "classroom" and row.external_id:
                store.set_disposition(row.external_id, state)

        def ignore_assignment(task: str) -> str:
            found = store.find_task(task)
            if found is None:
                return f"No task matching {task!r}."
            if found.source != "classroom" or not found.external_id:
                return (f"{found.title} is not Classroom work — complete or "
                        f"delete it instead of ignoring it.")
            settle(found.id, "ignored")
            store.complete_task(found.id, by="him")
            return (f"Ignoring {found.title}. It will not come back on the next "
                    f"sync; say restore to undo.")

        def restore_assignment(task: str) -> str:
            found = store.find_task(task, include_done=True)
            if found is None:
                return f"No task matching {task!r}."
            if found.external_id:
                store.clear_disposition(found.external_id)
            store.reopen_task(found.id)
            return f"{found.title} is back on the board."

        def complete_task(task: str) -> str:
            found = store.find_task(task)
            if found is None:
                return f"No open task matching {task!r}."
            settle(found.id, "done")
            done = store.complete_task(found.id)
            return f"completed {done.title}" if done else f"{found.title} was already done"

        def update_task(task: str, **changes: str) -> str:
            found = store.find_task(task)
            if found is None:
                return f"No task matching {task!r}."
            updated = store.update_task(found.id, **changes)
            if updated and changes.get("due") and updated.source == "classroom":
                # Say so now rather than letting him find out. Classroom owns
                # the date on Classroom work and the next sync restores it —
                # which is what he wants — but moving it silently meant he went
                # looking for a task that was not where he had just put it.
                return (f"updated {updated.title}, but it is Classroom work: the "
                        f"next sync restores the teacher's date "
                        f"({updated.due}). Tell him that rather than implying "
                        f"the move sticks.")
            return f"updated {updated.title}" if updated else "nothing to change"

        t.add("complete_task", "Mark a task done. Only when he says it is done.",
              complete_task,
              params={"task": {"type": "string", "description": "id or part of the title"}},
              required=["task"], background=False)
        t.add("ignore_assignment",
              "Drop a Classroom assignment for good — he is not doing it. "
              "Different from completing it: this says it was never going to "
              "happen. It stays gone across syncs.",
              ignore_assignment,
              params={"task": {"type": "string"}}, required=["task"],
              background=False)
        t.add("restore_assignment",
              "Put back something ignored or marked done by mistake.",
              restore_assignment,
              params={"task": {"type": "string"}}, required=["task"],
              background=False)

        t.add("update_task", "Change a task's title, subject, due date or priority.",
              update_task,
              params={"task": {"type": "string"}, "title": {"type": "string"},
                      "subject": {"type": "string"}, "due": {"type": "string"},
                      "priority": {"type": "string"}},
              required=["task"])

        t.add("remember",
              "Store something about him that will still be true later — the "
              "shape of his weeks, a standing commitment, a preference. Not a "
              "record of what you just did: a lock has a lock, a stand-down has "
              "stand_down, a due date has the task. Writing those here creates a "
              "second copy that drifts from the real one. Unmarked notes expire "
              "tonight; set standing for the recurring shape of his life.",
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

        def lock_phone(minutes: float = 60, reason: str = "",
                       start_in_minutes: float = 0,
                       escalate_minutes: float = 0,
                       escalations: float = 0) -> str:
            if not 5 <= minutes <= 8 * 60:
                return "Error: minutes must be between 5 and 480."
            if not 0 <= start_in_minutes <= 12 * 60:
                return "Error: start_in_minutes must be between 0 and 720."
            if escalations and not escalate_minutes:
                return "Error: escalations needs escalate_minutes."
            begins = clock.now() + timedelta(minutes=float(start_in_minutes))
            until = begins + timedelta(minutes=float(minutes))
            store.set_lock(until, reason or "he asked to be locked in",
                           starts=begins,
                           escalate_minutes=int(escalate_minutes),
                           escalations=int(escalations))
            # A fresh lock is unconfirmed by definition; clear any stale
            # report so the model is not told the *previous* lock's outcome.
            self.store.set_lock_applied(version=-1, shielded=False)
            pushed = self.wake_phone("lock")
            when = ("now" if start_in_minutes < 1
                    else f"at {begins:%H:%M}")
            tail = ""
            if escalations and escalate_minutes:
                tail = (f" If he has not started anything by {until:%H:%M} it "
                        f"extends by {int(escalate_minutes)} minutes, up to "
                        f"{int(escalations)} more time(s); starting a task ends "
                        f"it early.")
            return (
                f"Lock published: starts {when}, until {until:%H:%M}.{tail} "
                + ("The phone was pushed to apply it — but a push is a request, "
                   "not a receipt, so do not tell him it is on."
                   if pushed else
                   "The phone could not be pushed, so it applies on its next "
                   "wake, which may be hours. Do not tell him it is on.")
                + " He can always override, and an override you argue with is "
                  "an app he deletes."
            )

        def unlock_phone() -> str:
            if store.lock() is None:
                return "No lock is set."
            store.clear_lock("agent lifted it")
            self.wake_phone("unlock")
            return "Lock lifted. The phone releases on its next wake."

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

        t.add("lock_phone",
              "Block his distracting apps. Publishes the lock; the phone "
              "applies it on its next wake, so it is never instant and you must "
              "not tell him it already is. Can start later and can extend "
              "itself — say only what you actually set here, never a schedule "
              "the call did not include. Only when he asks or has agreed: "
              "locking him out unasked is how the app gets deleted.",
              lock_phone,
              params={"minutes": {"type": "number", "description": "5 to 480"},
                      "reason": {"type": "string"},
                      "start_in_minutes": {
                          "type": "number",
                          "description": "delay before it begins; 0 is now"},
                      "escalate_minutes": {
                          "type": "number",
                          "description": "extend by this if he still has not "
                                         "started when it lapses"},
                      "escalations": {
                          "type": "number",
                          "description": "how many times it may extend"}})
        t.add("unlock_phone", "Lift a lock you set.", unlock_phone)

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
        self.renew_task_lock()
        self.escalate_lock_if_idle()
        self.transcript.append("tick")
        brief = self.brief_due()
        out = self.turn(background=True, extra=self._brief_instruction() if brief else "")
        # Recorded from what happened, not from what was asked. brief_due reads
        # this, so the once-a-day guarantee is a row in the transcript rather
        # than a sentence in the prompt — which is what let it send the brief at
        # 16:02, ask "want the brief?" at 16:12 and send it again at 16:22.
        if brief and getattr(out, "spoke", False):
            # Keep the text, not just the fact. The app shows the brief as a
            # card he dismisses rather than leaving it to be scrolled past in
            # the chat thread — v1 queued it in an inbox the app collected and
            # acked, and losing that is why it stopped feeling like a briefing.
            spoken = next((e.payload.get("text", "")
                           for e in reversed(self.transcript.window(1))
                           if e.kind == "message_out"), "")
            self.transcript.append("brief_sent", text=spoken)
        return out

    def brief_card(self) -> dict | None:
        """Today's brief for the app, and whether he has dismissed it.

        Delivered *and* shown: the channel message is the notification, this is
        the thing he opens the app to read. Acknowledgement is a separate row
        so a reinstall or a second device cannot resurrect a brief he has
        already dealt with.
        """
        today = clock.day_key()
        sent = next((e for e in reversed(self.transcript.window(1))
                     if e.kind == "brief_sent" and e.day == today), None)
        if sent is None or not (sent.payload.get("text") or "").strip():
            return None
        acked = any(e.kind == "brief_acked" and e.day == today
                    for e in self.transcript.window(1))
        return {"text": sent.payload["text"], "at": sent.at, "acked": acked}

    def ack_brief(self) -> bool:
        if self.brief_card() is None:
            return False
        self.transcript.append("brief_acked")
        return True

    def _lock_truth(self) -> str:
        """What is actually true about the phone, not what was asked for.

        v1 kept the desired mode and the applied state in separate documents
        and compared versions, precisely so Argon could not claim a block it
        had only published. Without that it told him "locked for 60 minutes
        starting 20:30" while the phone had never heard of it.
        """
        rec = self.store.lock_record()
        if rec is None:
            return ""
        applied = self.store.lock_applied() or {}
        wanted, got = rec.get("version", 0), applied.get("version", -1)
        when = f"{rec['from_at']:%H:%M}–{rec['until_at']:%H:%M}"
        if applied.get("error") and got == wanted:
            return (f"LOCK {when} was REFUSED by the phone: {applied['error']}. "
                    f"It is not blocking. Say so if it comes up; do not repeat "
                    f"the request expecting a different answer.")
        if got != wanted:
            return (f"LOCK {when} is published but the phone has NOT confirmed "
                    f"it. You may say you have asked for it; you may not say it "
                    f"is on.")
        return (f"LOCK {when} confirmed by the phone"
                + (" and blocking now." if applied.get("shielded")
                   else ", not blocking yet."))

    def publish_lock_transition(self) -> None:
        """Push the phone when the lock turns on or off by itself.

        Every other path pushes because something called it. These two happen
        because time passed: a lock published at 19:28 to begin at 20:30 sent
        its only push at 19:28, and a lock that simply lapses sends none at
        all — so the shield stayed up until the phone next happened to wake.
        """
        rec = self.store.lock_record()
        now = clock.now()
        live = bool(rec and rec["from_at"] <= now < rec["until_at"])
        if live == self._lock_was_live:
            return
        self._lock_was_live = live
        self.wake_phone("lock started" if live else "lock ended")

    def renew_task_lock(self) -> None:
        """Keep a running task's block alive while he is still on it.

        v1 renewed on every status read, for the reason the fixed window gets
        wrong: a session that runs past the window drops the shield underneath
        him mid-work, and he did not stop, so nothing told it to. Renewed only
        while the task that raised it is still started — the moment he stops or
        finishes, end_task clears it instead.
        """
        rec = self.store.lock_record()
        if rec is None or rec.get("source") != "task":
            return
        task = self.store.task(rec.get("task_id") or "")
        if task is None or task.done or not task.started:
            return
        if (rec["until_at"] - clock.now()).total_seconds() > RENEW_WITHIN_MINUTES * 60:
            return
        self.store.set_lock(clock.now() + timedelta(hours=TASK_LOCK_HOURS),
                            rec.get("reason", "working"),
                            source="task", task_id=task.id)
        self.wake_phone("lock renewed")

    def escalate_lock_if_idle(self) -> None:
        """Extend a lapsed "until you start" lock when nothing has begun.

        The agent promised this out loud — "I'll extend by 30 minutes up to two
        times if you haven't said you started" — with no mechanism behind it.
        Either the promise goes or the mechanism does; he wanted the mechanism.

        Starting anything ends it, which is the whole point: the lock exists to
        get him started, so the moment he does it has done its job.
        """
        rec = self.store.lock_record()
        if rec is None or rec.get("escalations_left", 0) <= 0:
            return
        now = clock.now()
        if now < rec["until_at"]:
            return                      # still in force, nothing to do
        if any(t.started for t in self.store.tasks()):
            self.store.clear_lock("he started something")
            self.wake_phone("unlock")
            return
        if self.store.extend_lock(int(rec["escalate_minutes"])):
            self.wake_phone("lock extended")

    def brief_due(self) -> bool:
        """Whether the after-school brief still owes him one today.

        A school day, past the hour his day starts, and not already sent. The
        transcript is the record: a restart, a redeploy or a second tick inside
        the same minute all see the same answer.
        """
        now = clock.now()
        if now.weekday() > 4 or now.hour < schedule.WINDOW_START_HOUR:
            return False
        today = clock.day_key()
        return not any(e.kind == "brief_sent" and e.day == today
                       for e in self.transcript.window(1))

    def _brief_instruction(self) -> str:
        """The one turn a day that is asked to speak without being spoken to.

        Stated rather than left to the model to notice the hour: every version
        that relied on it noticing either skipped the brief or sent three.
        """
        return (
            "THIS TURN IS THE AFTER-SCHOOL BRIEF. Send exactly one message, "
            "now, using say. It is a one-way secretary brief: what is due "
            "today and tomorrow, any real calendar conflict, and anything a "
            "teacher posted that is not on the board. Two or three items, "
            "chronological. Do not ask him anything, do not offer to send it, "
            "do not propose a plan, and do not list the overdue backlog. If "
            "there is genuinely nothing worth saying, say nothing and call no "
            "tool — a brief with no content is worse than silence."
        )

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
                # Outside the tick gate on purpose. A lock starting or lapsing
                # is a clock event, not a conversation, and the evening window
                # has nothing to do with whether the phone needs telling.
                self.publish_lock_transition()
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

        # An inbound message refreshes a stale board before it is answered.
        # Syncing lived only inside the evening tick, so from Friday night to
        # Sunday afternoon the board froze and he was answered from it.
        fresh = Runtime(config.Config())
        calls: list[int] = []
        fresh.sync_classroom = lambda: calls.append(1) or "synced"  # type: ignore[method-assign]

        assert fresh._last_classroom_sync is None
        fresh.sync_if_stale()
        assert calls == [1], "a board never synced must be synced before answering"

        fresh._last_classroom_sync = clock.now()
        fresh.sync_if_stale()
        assert calls == [1], "a board synced just now is left alone"

        fresh._last_classroom_sync = clock.now() - timedelta(
            minutes=CLASSROOM_SYNC_MINUTES + 1)
        fresh.sync_if_stale()
        assert calls == [1, 1], "and a stale one is refreshed however late it is"

        # The brief is owed once a day and the transcript is what says so.
        import argon.clock as _c
        _c.set_for_test(datetime(2026, 9, 16, 17, 0, tzinfo=_c.TZ))   # Wednesday
        briefer = Runtime(config.Config())
        assert briefer.brief_due() is True, "a school afternoon owes him a brief"
        briefer.transcript.append("brief_sent")
        assert briefer.brief_due() is False, "and owes exactly one"
        _c.set_for_test(datetime(2026, 9, 16, 11, 0, tzinfo=_c.TZ))
        assert Runtime(config.Config()).brief_due() is False, "not before 4pm"
        _c.set_for_test(datetime(2026, 9, 19, 17, 0, tzinfo=_c.TZ))   # Saturday
        assert Runtime(config.Config()).brief_due() is False, "not at the weekend"
        _c.set_for_test(None)

        # The brief is shown once and dismissed once.
        carded = Runtime(config.Config())
        assert carded.brief_card() is None, "no brief today, nothing to show"
        carded.transcript.append("brief_sent", text="Two things due tonight.")
        card = carded.brief_card()
        assert card and card["text"].startswith("Two things") and card["acked"] is False
        assert carded.ack_brief() is True
        assert carded.brief_card()["acked"] is True, "dismissal survives the next read"

        # A task's block belongs to that task. Finishing it clears it;
        # finishing a different one does not, and neither does the task's
        # title changing underneath it.
        owner = Runtime(config.Config())
        a = owner.store.add_task("Essay")
        b = owner.store.add_task("Reading")
        owner.begin_task(a.id)
        assert owner.store.lock() is not None
        owner.end_task(b.id, done=True)
        assert owner.store.lock() is not None, "another task must not lift it"
        owner.store.update_task(a.id, title="Essay (revised)")
        owner.end_task(a.id, done=True)
        assert owner.store.lock() is None, "its own task lifts it, renamed or not"

        # A lock he asked for himself outlives a task ending.
        mine = Runtime(config.Config())
        c = mine.store.add_task("Essay")
        mine.store.set_lock(clock.now() + timedelta(hours=1), "he asked")
        mine.begin_task(c.id)
        mine.end_task(c.id, done=True)
        assert mine.store.lock() is not None, "his own lock-in survives"

        # A version never repeats, so a stale report cannot be read as a
        # report about the next lock. Clearing used to reset the counter.
        seqs = Runtime(config.Config())
        seqs.store.clear_lock()
        seqs.store.set_lock(clock.now() + timedelta(hours=1), "one")
        first = seqs.store.lock_record()["version"]
        seqs.store.clear_lock()
        seqs.store.set_lock(clock.now() + timedelta(hours=1), "two")
        assert seqs.store.lock_record()["version"] > first, \
            "a cleared lock must not hand its version to the next one"
        seqs.store.clear_lock()

        # His decision about a Classroom item outlives the import. Ported from
        # v1: "done" because plenty of coursework has nothing to submit, and
        # "ignored" because some of it he is simply not doing.
        disp = Runtime(config.Config())
        item = {"id": "cw-ignored", "title": "Reminder: study for midterm",
                "course": "APUSH", "due": "2026-09-30"}
        disp.store.add_task(item["title"], subject=item["course"],
                            due=item["due"], source="classroom",
                            external_id=item["id"])
        found = disp.store.find_task("midterm")
        assert found is not None
        assert "Ignoring" in disp.tools.call(
            "ignore_assignment", {"task": found.id}, background=False)
        assert disp.store.disposition("cw-ignored") == "ignored"
        assert disp.store.find_task("midterm") is None, "off the board"

        # A re-import must not resurrect it.
        disp.store.add_task(item["title"], subject=item["course"],
                            due=item["due"], source="classroom",
                            external_id=item["id"])
        settled = disp.store.disposition("cw-ignored")
        assert settled == "ignored", "the decision survives the import"

        # And he can undo it.
        assert "back on the board" in disp.tools.call(
            "restore_assignment", {"task": found.id}, background=False)
        assert disp.store.disposition("cw-ignored") is None
        assert disp.store.find_task("midterm") is not None

        # The visible Classroom window is not completion evidence. Old owed
        # work can be hidden from the phone without being marked submitted.
        sync_truth = Runtime(config.Config())
        old = sync_truth.store.add_task("Old math homework", source="classroom",
                                        external_id="cw-old", due="2026-08-01")
        original_account_for = google.account_for
        original_outstanding = google.outstanding_assignments
        google.account_for = lambda capability, accounts: "school"  # type: ignore[assignment]
        google.outstanding_assignments = lambda account: ([], {"cw-old"}, [])  # type: ignore[assignment]
        try:
            sync_truth.sync_classroom()
            assert not sync_truth.store.task(old.id).done, \
                "outside the display window is still outstanding"
            google.outstanding_assignments = lambda account: ([], set(), [])  # type: ignore[assignment]
            sync_truth.sync_classroom()
            assert sync_truth.store.task(old.id).done, \
                "only absence from Classroom's owed set closes the task"
        finally:
            google.account_for = original_account_for  # type: ignore[assignment]
            google.outstanding_assignments = original_outstanding  # type: ignore[assignment]

        # Argon may not claim a lock the phone has not confirmed. v1 kept
        # desired and applied apart and compared versions for exactly this.
        truth = Runtime(config.Config())
        truth.store.clear_lock()      # earlier cases share this store
        assert truth._lock_truth() == "", "no lock, nothing to say"
        truth.store.set_lock(clock.now() + timedelta(hours=1), "start work")
        line = truth._lock_truth()
        assert "NOT confirmed" in line and "may not say it is on" in line, line

        v = truth.store.lock_record()["version"]
        truth.store.set_lock_applied(version=v, shielded=True)
        assert "confirmed by the phone and blocking now" in truth._lock_truth()

        truth.store.set_lock_applied(version=v, shielded=False,
                                     error="Screen Time not authorised")
        assert "REFUSED" in truth._lock_truth()

        # Extending must not bump the version: a new version makes the phone
        # re-apply, and re-applying restarts the window being extended.
        truth.store.set_lock(clock.now() + timedelta(minutes=5), "x",
                             escalate_minutes=10, escalations=1)
        before = truth.store.lock_record()["version"]
        clock.set_for_test(clock.now() + timedelta(minutes=6))
        truth.store.extend_lock(10)
        assert truth.store.lock_record()["version"] == before, \
            "extending in place must not look like a new lock"
        clock.set_for_test(None)

        # A dead channel falls through to the next; a live one ends it. The
        # count matters: fanning out delivered the same sentence twice, once to
        # the phone and once to Discord, and reading it in one left it unread
        # in the other.
        fallback = Runtime(config.Config())
        landed: list[str] = []
        fallback.add_channel(lambda _: (_ for _ in ()).throw(RuntimeError("push down")))
        fallback.add_channel(landed.append)
        assert fallback.deliver("x") == [], "the fallback channel means he got it"
        assert landed == ["x"], landed
        kinds = [e.kind for e in fallback.transcript.window(3)]
        assert "delivery_fellback" in kinds, "a broken channel stays findable"
        assert "delivery_failed" not in kinds

        # A working first channel stops there — the second never sees it.
        quiet = Runtime(config.Config())
        first, second = [], []
        quiet.add_channel(first.append)
        quiet.add_channel(second.append)
        assert quiet.deliver("once") == []
        assert first == ["once"] and second == [], (first, second)

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
