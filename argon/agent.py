"""One turn: build context, ask the model, run what it asked for.

This module is what replaced ``pick_occasion``.  There is no table of reasons to
speak and no gate deciding whether a moment qualifies.  A tick appends an event
and asks; the model reads two days of transcript — including what it already
said and whether he answered — and decides.  Restraint comes from that
visibility, not from a counter.

**Background turns and interactive turns differ in exactly one way.**

- *Interactive* (he sent something and is waiting): the model's text is the
  reply.  He asked a question; answering it is the whole job.
- *Background* (a tick): the model's text is thinking and is discarded.  Only an
  explicit ``say`` reaches him.

That asymmetry is deliberate.  Every historical spam incident was a background
turn whose output was delivered because it existed, and the 4 PM brief that
arrived as ``Error code: 504`` was the same failure wearing a different hat.  On
a background turn there is no path from "the model produced characters" to "his
phone buzzed".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from argon import budget, clock, context, provider
from argon.config import Config
from argon.store import Store
from argon.tools import Tools, parse_calls
from argon.transcript import Transcript

#: How many times one turn may call tools before it is cut off.  A model looping
#: on a failing tool should cost a bounded amount, not a month's budget.
MAX_STEPS = 8


@dataclass
class Outcome:
    """What a turn did.  Returned for logging and tests, not for him."""

    spoke: bool = False
    steps: int = 0
    cost: float = 0.0
    error: str = ""
    text: str = ""
    tools_used: list[str] = field(default_factory=list)


class Agent:
    def __init__(self, cfg: Config, transcript: Transcript, store: Store,
                 tools: Tools, system: str) -> None:
        self.cfg = cfg
        self.t = transcript
        self.store = store
        self.tools = tools
        self.system = system

    def turn(self, *, background: bool, extra: str = "") -> Outcome:
        """Run one turn to completion.

        *extra* is live state for the prompt tail. It is a parameter rather
        than something the caller patches into ``context.build``: the runtime
        used to swap that module global for the duration of a turn, and two
        overlapping turns left it pointing at a leaked closure forever.
        """
        self._background = background
        self._went_quiet = False
        try:
            return self._turn(background=background, extra=extra)
        finally:
            # Scoped to the turn, not left set. say() consults this, and a flag
            # that outlives its turn silently gates the next caller.
            self._background = False

    def _turn(self, *, background: bool, extra: str = "") -> Outcome:
        out = Outcome()
        messages = context.build(self.t, self.system, extra=extra)
        schemas = self.tools.schemas(background=background)

        for step in range(MAX_STEPS):
            try:
                reply = provider.complete(
                    self.cfg.provider, messages, tools=schemas,
                    cap=self.cfg.monthly_cap_usd,
                )
            except budget.BudgetExceeded as e:
                # Not an error to report to him here; the runtime sends the one
                # notification. Going quiet is the behaviour he chose.
                out.error = str(e)
                self.t.append("budget_stop", summary=str(e))
                return out
            except provider.ProviderError as e:
                out.error = str(e)
                self.t.append("provider_error", summary=str(e))
                return out

            out.cost += reply.cost
            out.steps = step + 1

            if not reply.tool_calls:
                out.text = reply.text
                # Interactive: the text is the answer. Background: it was
                # thinking, and thinking is not delivered.
                #
                # `not out.spoke` matters. A model that calls `say` and then
                # also signs off with the same sentence would otherwise send it
                # twice — which is what happened the first time Discord
                # delivery started working.
                if reply.text and not background and not out.spoke:
                    self.say(reply.text)
                    out.spoke = True
                return out

            messages.append({
                "role": "assistant",
                "content": reply.text or None,
                "tool_calls": reply.tool_calls,
            })
            for call_id, name, args in parse_calls(reply.tool_calls):
                result = self.tools.call(name, args, background=background)
                out.tools_used.append(name)
                if name.split("<|")[0].strip() == "say" and not result.startswith("Error"):
                    out.spoke = True
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result,
                })

        out.error = f"stopped after {MAX_STEPS} tool steps"
        self.t.append("turn_truncated", summary=out.error)
        return out

    # -- delivery ---------------------------------------------------------
    def say(self, text: str) -> str:
        """The only path to him.

        Records what actually happened, which is not the same as what was
        attempted. A `message_out` means he received it; a total delivery
        failure is recorded as `undelivered` instead, and reported to the model
        as an error.

        Reporting success for a message that reached nobody is how nine
        rewordings went out in twenty seconds: the model saw "sent", then saw
        the failure, and hunted for phrasing that would work. The model must be
        told plainly that the channel is broken and that retrying is pointless.
        """
        text = strip_ids((text or "").strip())
        if not text:
            return "Error: empty message not sent"
        if getattr(self, "_background", False):
            if self._went_quiet:
                return ("Error: you called stand_down this turn. Going quiet is "
                        "the action; announcing it is not going quiet. He was "
                        "not messaged.")
            if (wait := self._unanswered_gate()) is not None:
                return wait

        errors = self.deliver(text)
        if errors:
            self.t.append("undelivered", text=text, summary="; ".join(errors)[:200])
            return (f"NOT DELIVERED — {'; '.join(errors)[:200]}. He did not receive this. "
                    f"The channel is misconfigured; rewording will not help. "
                    f"Do not call say again this turn.")
        self.t.append("message_out", text=text)
        return "sent"

    def _unanswered_gate(self) -> str | None:
        """Refuse an unprompted message when the last ones went unanswered.

        On 16 Sep this sent the after-school brief at 16:02, asked "want the
        after-school brief now?" at 16:12, then sent the brief again at 16:22 —
        three messages in twenty minutes, none answered. The prompt already
        says not to; the prompt has said not to since the first draft. A rule
        the model weighs against context is not a rule, so this is code.

        Silence is earned back by him replying, not by time alone: the counter
        is consecutive sends since his last word, so one reply reopens the
        channel immediately.
        """
        # A deadline the server can see buys the interruption. Urgency is a
        # property of the world, not of how long since the last message: "All
        # Project Sync starts in 15 minutes" is worth interrupting for whether
        # or not a nudge went out ten minutes ago.
        #
        # Checked here rather than declared by the model on purpose. A tier the
        # model asserts is a tier the model will assert every time — the same
        # failure as the task ids and the stand-down announcements. It cannot
        # claim urgency; the clock either shows a deadline or it does not.
        if self.urgent_now():
            return None

        today = clock.day_key()
        sent_today = sum(1 for e in self.t.window(2)
                         if e.day == today and e.kind == "message_out")
        if sent_today >= DAILY_UNPROMPTED_CAP:
            return (f"Error: {sent_today} messages already today, which is the "
                    f"cap. Nothing further unprompted until tomorrow or until "
                    f"he speaks. Do not call say again.")

        unanswered, last_out = 0, None
        for e in reversed(self.t.window(2)):
            if e.day != today:
                break
            if e.kind == "message_in":
                break
            if e.kind == "message_out":
                unanswered += 1
                last_out = last_out or e
        if not unanswered or last_out is None:
            return None
        if unanswered >= len(UNANSWERED_BACKOFF) + 1:
            return ("Error: he has not answered any of your "
                    f"{unanswered} messages today. Nothing further goes out "
                    "until he speaks. Do not call say again.")
        need = UNANSWERED_BACKOFF[unanswered - 1]
        try:
            since = clock.now() - datetime.fromisoformat(last_out.at)
        except ValueError:
            return None
        if since < need:
            left = int((need - since).total_seconds() // 60)
            return (f"Error: your last message is {int(since.total_seconds() // 60)} "
                    f"minutes old and unanswered. Not sent — nothing unprompted "
                    f"for another {left} minutes unless he speaks first.")
        return None

    #: Replaced by the runtime with the real clock. Returns why the next few
    #: minutes are time-critical, or None. The default is None, so a bare Agent
    #: in a test is never accidentally urgent.
    def urgent_now(self) -> str | None:  # pragma: no cover - overridden
        return None

    #: Replaced by the runtime with the real channels. Returns the delivery
    #: failures; empty means it landed. The default accepts, so a bare Agent in
    #: a test is not reporting a broken channel.
    def deliver(self, text: str) -> list[str]:  # pragma: no cover - overridden
        return []

    def receive(self, text: str, *, source: str = "ios", extra: str = "") -> Outcome:
        """He said something.  Record it, then answer."""
        self.t.append("message_in", text=text, source=source)
        return self.turn(background=False, extra=extra)



#: What an unanswered message costs the next one. Index 0 is the wait after one
#: unanswered send, index 1 after two; past the end of this nothing goes out
#: until he speaks. Deliberately steep — he replies rarely, and a missed nudge
#: costs far less than him muting the channel.
UNANSWERED_BACKOFF = (timedelta(minutes=45), timedelta(hours=3))

#: Hard ceiling on unprompted messages in a day. The backoff bounds how close
#: together they land; this bounds how many there are at all, which is the
#: number the attention research actually converges on (three to five, enforced
#: as a constraint rather than a target). A verified deadline still gets
#: through — a missed event is not what this is protecting him from.
DAILY_UNPROMPTED_CAP = 4

#: An argon: link, or a bare task id (uuid4().hex[:12]). The link alternative
#: comes first so the id *inside* argon:task/<id> matches as part of the link
#: and is handed back untouched — a lookbehind cannot do this, because what
#: precedes the id there is "task/", not "argon:".
_BARE_ID = re.compile(r"argon:\S+|\b[0-9a-f]{12}\b")

#: "— task id ee4723185a15", "(task id ee4723185a15)", "task id: ee4723185a15".
_LABELLED_ID = re.compile(
    r"\s*[\(\[]?\s*[—–-]?\s*task\s+id[:\s]+[0-9a-f]{12}\s*[\)\]]?", re.I)


def strip_ids(text: str) -> str:
    """Remove task ids from anything he is about to read.

    The system prompt has said "never print a task id as text" from the start
    and the model prints them anyway — "HW 18 — task id ee4723185a15" went out
    to his phone. This is the same lesson as the delivery guard: a rule in the
    prompt is a suggestion the model weighs against everything else in context,
    a rule in code is a rule. An id is plumbing; it means nothing to him and it
    makes the message read like a database dump.

    Ids inside an ``argon:`` link are left alone — there they are the payload
    the app taps on, not text he reads.
    """
    text = _LABELLED_ID.sub("", text)
    text = _BARE_ID.sub(lambda m: m[0] if m[0].startswith("argon:") else "", text)
    # The removal leaves the dangling punctuation that introduced it.
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+[—–-]\s*([.\n]|$)", r"\1", text)
    return text.strip()


def _selftest() -> None:
    """Run with ``python -m argon.agent``.  Uses a fake provider, no network."""
    import os
    import tempfile
    from datetime import datetime
    from pathlib import Path

    from argon import clock

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ARGON_HOME"] = tmp
        clock.set_for_test(datetime(2026, 9, 14, 18, 0, tzinfo=clock.TZ))

        t = Transcript(Path(tmp) / "t.db")
        store = Store(Path(tmp) / "s.db", t)
        tools = Tools(t)
        cfg = Config()

        agent = Agent(cfg, t, store, tools, "SYS")
        delivered: list[str] = []

        def deliver(text: str) -> list[str]:
            delivered.append(text)
            return []

        agent.deliver = deliver
        tools.add("say", "Send him a message.", agent.say,
                  params={"text": {"type": "string"}}, required=["text"])

        scripted: list[provider.Reply] = []
        provider.complete = lambda *a, **k: scripted.pop(0)  # type: ignore[assignment]

        # Background turn, plain text: thinking. Nothing is delivered.
        scripted.append(provider.Reply(text="he seems fine, I'll stay quiet"))
        out = agent.turn(background=True)
        assert out.spoke is False and delivered == [], delivered
        assert out.text.startswith("he seems fine")

        # Background turn that calls say: delivered.
        scripted.append(provider.Reply(tool_calls=[
            {"id": "1", "function": {"name": "say", "arguments": '{"text":"two things due"}'}}]))
        scripted.append(provider.Reply(text=""))
        out = agent.turn(background=True)
        assert out.spoke is True and delivered == ["two things due"]

        # Interactive turn: plain text is the reply.
        scripted.append(provider.Reply(text="nothing due tomorrow"))
        out = agent.receive("what's due?")
        assert out.spoke is True and delivered[-1] == "nothing due tomorrow"

        kinds = [e.kind for e in t.window(2)]
        assert kinds.count("message_in") == 1 and kinds.count("message_out") == 2

        # Calling say and then signing off with text must not send twice.
        scripted.append(provider.Reply(tool_calls=[
            {"id": "9", "function": {"name": "say", "arguments": '{"text":"only once"}'}}]))
        scripted.append(provider.Reply(text="only once"))
        count = len(delivered)
        out = agent.receive("say it")
        assert delivered[count:] == ["only once"], delivered[count:]

        # A provider failure never reaches him.
        def boom(*a, **k):
            raise provider.ProviderError("HTTP 504: gateway timeout")
        provider.complete = boom  # type: ignore[assignment]
        before = list(delivered)
        out = agent.turn(background=True)
        assert out.error.startswith("HTTP 504") and delivered == before, "504 must not be delivered"
        assert "provider_error" in [e.kind for e in t.window(2)]

        # The budget stop is quiet by design.
        def capped(*a, **k):
            raise budget.BudgetExceeded("monthly cap reached")
        provider.complete = capped  # type: ignore[assignment]
        out = agent.turn(background=True)
        assert out.spoke is False and delivered == before
        assert "budget_stop" in [e.kind for e in t.window(2)]

        # An empty say is refused rather than sending a blank message.
        assert agent.say("   ").startswith("Error")
        assert delivered == before

        # A channel that accepts nothing must not be reported as success.
        agent.deliver = lambda text: ["discord: channel not visible"]
        result = agent.say("did this land?")
        assert result.startswith("NOT DELIVERED"), result
        assert "rewording will not help" in result
        kinds = [e.kind for e in t.window(2)]
        assert "undelivered" in kinds
        assert not any(e.kind == "message_out" and e.payload.get("text") == "did this land?"
                       for e in t.window(2)), "a failed send is not a message he received"

        clock.set_for_test(None)
        del os.environ["ARGON_HOME"]
    # Three messages in twenty minutes, none answered, is the failure this
    # exists to stop. The gate counts sends since his last word, so a reply
    # reopens the channel at once.
    t2 = Transcript(Path(tmp) / "gate.db")
    a2 = Agent(cfg, t2, store, Tools(t2), "SYS")
    a2.deliver = lambda text: []
    a2._background, a2._went_quiet = True, False
    assert a2.say("first") == "sent"
    assert a2.say("second").startswith("Error"), "a second unprompted send must wait"
    t2.append("message_in", text="ok")
    assert a2.say("third") == "sent", "his reply reopens the channel"

    # A verified deadline buys the interruption — this is the case the flat
    # backoff got wrong, swallowing "your event starts in fifteen minutes".
    assert a2.say("fourth").startswith("Error")
    a2.urgent_now = lambda: "All Project Sync starts in 12 minutes"
    assert a2.say("your 19:00 starts in 12 minutes") == "sent"

    # The cap bounds volume, which the backoff does not: spacing alone lets a
    # long evening carry any number of them.
    a2.urgent_now = lambda: None
    while True:
        t2.append("message_in", text="go on")       # reopen, so only the cap bites
        r = a2.say("another")
        if r != "sent":
            break
    assert "cap" in r, r
    sent = sum(1 for e in t2.window(2) if e.kind == "message_out")
    assert sent == DAILY_UNPROMPTED_CAP, sent

    # A deadline still gets through the cap — a missed event is not what the
    # cap protects him from.
    a2.urgent_now = lambda: "Robotics starts in 8 minutes"
    assert a2.say("robotics in 8") == "sent"

    # Going quiet is the action; announcing it is not going quiet.
    a2._went_quiet = True
    assert a2.say("standing down for tonight").startswith("Error")

    # Ids are stripped in code because the prompt rule did not hold.
    assert strip_ids("HW 18 — task id ee4723185a15. Nothing else.") \
        == "HW 18. Nothing else."
    assert strip_ids("Started it (task id 445d89ec04eb).") == "Started it."
    assert strip_ids("Open argon:task/445d89ec04eb now.") \
        == "Open argon:task/445d89ec04eb now.", "the link payload must survive"
    assert strip_ids("Nothing due today.") == "Nothing due today."

    print("agent selftest ok")


if __name__ == "__main__":
    _selftest()
