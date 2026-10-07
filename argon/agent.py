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

    def turn(self, *, background: bool, extra: str = "", request: str = "") -> Outcome:
        """Run one turn to completion.

        *extra* is live state for the prompt tail. It is a parameter rather
        than something the caller patches into ``context.build``: the runtime
        used to swap that module global for the duration of a turn, and two
        overlapping turns left it pointing at a leaked closure forever.
        """
        self._background = background
        self._went_quiet = False
        self._spoke_this_turn = False
        self._say_terminal = False
        try:
            return self._turn(background=background, extra=extra, request=request)
        finally:
            # Scoped to the turn, not left set. say() consults this, and a flag
            # that outlives its turn silently gates the next caller.
            self._background = False

    def _turn(self, *, background: bool, extra: str = "", request: str = "") -> Outcome:
        out = Outcome()
        messages = context.build(self.t, self.system, extra=extra)
        schemas = self.tools.schemas(background=background)
        if not background:
            messages.append({"role": "user", "content":
                             "This is an interactive turn. Answer only his current "
                             "request below; earlier conversations are history, not "
                             "pending instructions. After acting, acknowledge the "
                             "result instead of resuming an older task.\n\n" + request})

        nudged = False
        for step in range(MAX_STEPS):
            final_reply = not background and step == MAX_STEPS - 1
            if final_reply:
                messages.append({"role": "user", "content":
                                 "Finish this turn now: reply briefly to his current "
                                 "request using the results above. State any failure "
                                 "or remaining work honestly. Do not call more tools."})
            try:
                reply = provider.complete(
                    self.cfg.provider, messages, tools=[] if final_reply else schemas,
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
            if reply.fell_back:
                # A config error on the primary used to look exactly like the
                # primary working. Once per turn is enough to be seen.
                if step == 0:
                    self.t.append("provider_fallback",
                                  summary=f"answered by {reply.model}; {reply.fell_back}"[:300])

            if not reply.tool_calls:
                # He asked and is waiting, and the model finished on a tool
                # call's result with nothing to say. On 09-24 "Can you stop the
                # block" was acted on at 22:31 and answered at 23:24, by a tick.
                # One nudge, inside this turn only; it never reaches the
                # transcript.
                if (not background and not reply.text.strip() and not out.spoke
                        and not nudged and out.tools_used and not final_reply):
                    nudged = True
                    messages.append({"role": "assistant", "content": None,
                                     "tool_calls": [], "_items": reply.items})
                    messages.append({"role": "user", "content":
                                     "(Now reply to him — one or two sentences on what "
                                     "you did or found.)"})
                    continue
                if not background and not reply.text.strip() and not out.spoke:
                    out.error = "interactive turn ended without a reply"
                    self.t.append("turn_truncated", summary=out.error)
                    return out
                out.text = reply.text
                # Interactive: the text is the answer. Background: it was
                # thinking, and thinking is not delivered.
                #
                # `not out.spoke` matters. A model that calls `say` and then
                # also signs off with the same sentence would otherwise send it
                # twice — which is what happened the first time Discord
                # delivery started working.
                if reply.text and not background and not out.spoke:
                    result = self.say(reply.text)
                    out.spoke = result == "sent"
                    if not out.spoke:
                        out.text = ""
                        out.error = result
                return out

            messages.append({
                "role": "assistant",
                "content": reply.text or None,
                "tool_calls": reply.tool_calls,
                # The provider's own output, reasoning included, replayed as-is
                # on the next step. Lives only in this turn's list; the
                # transcript never stores it.
                "_items": reply.items,
            })
            stop_saying = ""
            for call_id, name, args in parse_calls(reply.tool_calls):
                result = self.tools.call(name, args, background=background)
                out.tools_used.append(name)
                if name.split("<|")[0].strip() == "say":
                    out.spoke = out.spoke or result == "sent"
                    # Rephrasing cannot change time, a spent attention budget,
                    # or a broken delivery channel. Don't pay for eight retries.
                    if self._say_terminal:
                        stop_saying = result
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result,
                })
            if stop_saying:
                out.error = stop_saying
                return out

        out.error = f"stopped after {MAX_STEPS} tool steps"
        self.t.append("turn_truncated", summary=out.error)
        return out

    # -- delivery ---------------------------------------------------------
    def _blocked(self, guard: str, reason: str) -> str:
        """Record which guard refused a message, and why.

        Six of these now stack in say(), and they are not equal: some compare
        the sentence against the database, some pattern-match English. The
        second kind is a stopgap shaped by the failures that happened to occur
        in one week, so it has to be possible to see whether each still earns
        its place — a guard that never fires is dead weight to delete, and one
        that fires daily means the cause upstream was never fixed.
        """
        self.t.append("guard_blocked", guard=guard, summary=reason[:200])
        return reason

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
        # Questions. He wants a routine, not an interview: a quarter of
        # everything Argon said carried one, and the shapes were "want the
        # after-school brief now?", "Quick brief or full todo list?" and two
        # clarifications in a row before setting a lock, which ended in "Dude
        # just figure it out". The prompt has forbidden this throughout.
        if asks_a_question(text):
            if getattr(self, "_background", False):
                return self._blocked("question_unprompted", "Error: not sent. An unprompted message may not ask him "
                        "anything — he did not start this conversation, so there "
                        "is nothing he owes an answer to. State it, act on the "
                        "sensible default, or say nothing.")
            if last_message_was_a_question(self.t):
                return self._blocked("question_twice", "Error: not sent. Your last message already asked him "
                        "something and this asks again. Take the most reasonable "
                        "reading and act on it; he can correct you in one word.")

        # An invented absence is the one error he cannot catch: an invented
        # task he notices, an invented "nothing due" he does not. The prompt
        # has forbidden this from the first draft and on 20 Sep it still said
        # APUSH had only two past reminders while two live APUSH tasks sat in
        # the board it had been handed. Checked against the board instead.
        # One message per turn. On 20 Sep finishing one task produced "Marked
        # InQuizitive Ch 6 (APUSH) done." and then "Nice — I marked InQuizitive
        # Ch 6 done and added a note." — two notifications, one action. If
        # there is more to say it belongs in the same message.
        if getattr(self, "_spoke_this_turn", False):
            return self._blocked("spoke_twice", "Error: you have already sent him a message this turn. "
                    "He does not need a second one for the same action. If "
                    "something is genuinely missing, it belonged in the first.")
        if (missed := unmentioned_live_work(text, self.store.tasks())):
            listed = "; ".join(f"{t.title} ({t.subject}) due {t.due}" for t in missed[:6])
            return self._blocked("invented_absence", "Error: not sent. That says nothing is there, but the board "
                    f"has: {listed}. Either name this work or drop the claim. "
                    "Do not tell him something is absent that the board lists.")
        if getattr(self, "_background", False):
            if self._went_quiet:
                return self._blocked("announced_quiet", "Error: you called stand_down this turn. Going quiet is "
                        "the action; announcing it is not going quiet. He was "
                        "not messaged.")
            if (wait := self._unanswered_gate()) is not None:
                self._say_terminal = True
                return self._blocked("too_soon_or_capped", wait)

        errors = self.deliver(text)
        if errors:
            self._say_terminal = True
            self.t.append("undelivered", text=text, summary="; ".join(errors)[:200])
            return (f"NOT DELIVERED — {'; '.join(errors)[:200]}. He did not receive this. "
                    f"The channel is misconfigured; rewording will not help. "
                    f"Do not call say again this turn.")
        self.t.append("message_out", text=text)
        self._spoke_this_turn = True
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

        # Only messages he did not ask for count against the budget. Counting
        # every message_out meant a conversation spent it: on 21 Sep an
        # evening of back-and-forth reached "8 messages already today" and
        # every proactive message for the rest of the night was refused —
        # including, at the moment it mattered, the ones about the lock he had
        # just set up. Talking to Argon must not buy silence from it.
        today = clock.day_key()
        unprompted = 0
        # False, not True: a day that opens with Argon speaking opens with an
        # unprompted message. Seeding this True let the first one of the day
        # through uncounted every day.
        answered = False
        for e in self.t.window(2):
            if e.day != today:
                continue
            if e.kind == "message_in":
                answered = True
            elif e.kind == "message_out":
                if not answered:
                    unprompted += 1
                answered = False
        if unprompted >= DAILY_UNPROMPTED_CAP:
            return (f"Error: {unprompted} unprompted messages already today, "
                    f"which is the cap. Nothing further until tomorrow or until "
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
        return self.turn(background=False, extra=extra, request=text)



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



#: Phrases that claim nothing is there. Deliberately narrow: these are the
#: shapes an invented absence actually took, not every possible negation.
_ABSENCE = re.compile(
    r"\b(?:nothing\s+(?:is\s+)?(?:due|open|outstanding|else|left|showing|scheduled)"
    r"|no\s+(?:other|further|more)\s+\w+"
    r"|the\s+only\s+\w+"
    r"|no\s+\w{0,12}\s*(?:assignments?|homework|work|tasks?)\s+(?:due|showing|open|left)?"
    r"|none\s+(?:due|open|outstanding))\b", re.I)

#: Words too common to identify a task by.
_STOPWORDS = frozenset("""the a an and or of for to in on at by with from your his
    due today tomorrow night reminder reminders study read watch complete finish
    chapter ch unit part page pages""".split())


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[0-9a-z\u3000-\u9fff]{2,}", text.lower())
            if w not in _STOPWORDS}


def unmentioned_live_work(text: str, tasks: list) -> list:
    """Live tasks a message denies by omission.

    Returns tasks whose *subject* the message names while claiming an absence,
    and whose own title it never mentions. A message that names the work is
    fine however it is phrased; a message that says "the only APUSH items are
    two past reminders" while APUSH has two live ones is not.
    """
    if not _ABSENCE.search(text):
        return []
    said = _keywords(text)
    missed = []
    for t in tasks:
        subject = (getattr(t, "subject", "") or "").strip()
        if not subject:
            continue
        # Only judge subjects he is being told about. A brief that omits a
        # class entirely is a different question, and not one a regex should
        # be deciding.
        subject_words = _keywords(subject)
        if not subject_words or not (subject_words & said):
            continue
        title_words = _keywords(getattr(t, "title", "") or "")
        if not title_words:
            continue
        # Half the distinctive words is enough: he is told "Inquizitive Ch 6"
        # for a task titled "Reminder:  Inquizitive Ch 6 due tonight!".
        hit = len(title_words & said)
        if hit * 2 < len(title_words):
            missed.append(t)
    return missed



def asks_a_question(text: str) -> bool:
    """Whether the message puts a question to him.

    A question mark inside a quoted assignment title is not Argon asking
    anything, so the mark has to end a sentence rather than merely appear.
    """
    return bool(re.search(r"\?(?:\s|[\)\]\u201d\"'])*$", text.strip(), re.M))


def last_message_was_a_question(transcript) -> bool:
    for e in reversed(transcript.window(1)):
        if e.kind == "message_in":
            return False
        if e.kind == "message_out":
            return asks_a_question(e.payload.get("text") or "")
    return False


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

        # He asked; the model acted and then said nothing. One nudge gets the
        # reply out in the same turn instead of from a tick an hour later.
        tools.add("unlock_phone", "release", lambda: "Release sent.")
        scripted.append(provider.Reply(tool_calls=[
            {"id": "u1", "function": {"name": "unlock_phone", "arguments": "{}"}}]))
        scripted.append(provider.Reply(text=""))
        scripted.append(provider.Reply(text="Sent the release; it's coming off."))
        count = len(delivered)
        out = agent.receive("can you stop the block")
        assert delivered[count:] == ["Sent the release; it's coming off."], delivered[count:]
        assert not scripted, "exactly one nudge"

        # Time/cap refusals end the turn; rephrasing does not buy permission.
        agent._unanswered_gate = lambda: "Error: capped. Do not call say again."
        scripted.append(provider.Reply(tool_calls=[
            {"id": "blocked", "function": {"name": "say", "arguments": '{"text":"another nudge"}'}}]))
        out = agent.turn(background=True)
        assert not out.spoke and "capped" in out.error and not scripted
        del agent._unanswered_gate

        # Delivery failure is never counted as a successful turn or brief.
        real_deliver = agent.deliver
        agent.deliver = lambda text: ["channel down"]
        agent._unanswered_gate = lambda: None
        scripted.append(provider.Reply(tool_calls=[
            {"id": "dead", "function": {"name": "say", "arguments": '{"text":"brief"}'}}]))
        out = agent.turn(background=True)
        assert not out.spoke and out.error.startswith("NOT DELIVERED")
        scripted.append(provider.Reply(text="interactive reply"))
        out = agent.receive("hello")
        assert not out.spoke and not out.text and out.error.startswith("NOT DELIVERED")
        agent.deliver = real_deliver
        del agent._unanswered_gate

        # Reserve the last step for an answer, even after a long tool loop.
        rounds = []
        def looping(*a, **kwargs):
            rounds.append(kwargs["tools"])
            if kwargs["tools"]:
                return provider.Reply(tool_calls=[
                    {"id": str(len(rounds)), "function": {"name": "unlock_phone", "arguments": "{}"}}])
            return provider.Reply(text="The release is sent.")
        provider.complete = looping
        out = agent.receive("Unblock")
        assert out.spoke and out.steps == MAX_STEPS and rounds[-1] == []
        assert out.tools_used == ["unlock_phone"] * (MAX_STEPS - 1)

        # A queued turn keeps its own request even after a newer one lands.
        t.append("message_in", text="newer request")
        def focused(cfg, messages, **kwargs):
            assert messages[-1]["content"].endswith("original request")
            return provider.Reply(text="Acknowledged.")
        provider.complete = focused
        assert agent.turn(background=False, request="original request").spoke

        # A reasoning-only final call is an observable error, not silent success.
        def empty_final(cfg, messages, **kwargs):
            if kwargs["tools"]:
                return provider.Reply(tool_calls=[
                    {"id": "loop", "function": {"name": "unlock_phone", "arguments": "{}"}}])
            return provider.Reply()
        provider.complete = empty_final
        out = agent.receive("Unblock")
        assert not out.spoke and out.error == "interactive turn ended without a reply"
        assert t.last("turn_truncated").payload["summary"] == out.error

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
    a2._spoke_this_turn = False   # these probe the backoff, not the per-turn rule
    assert a2.say("second").startswith("Error"), "a second unprompted send must wait"
    t2.append("message_in", text="ok")
    a2._spoke_this_turn = False
    assert a2.say("third") == "sent", "his reply reopens the channel"

    # A verified deadline buys the interruption — this is the case the flat
    # backoff got wrong, swallowing "your event starts in fifteen minutes".
    a2._spoke_this_turn = False
    assert a2.say("fourth").startswith("Error")
    a2.urgent_now = lambda: "All Project Sync starts in 12 minutes"
    a2._spoke_this_turn = False
    assert a2.say("your 19:00 starts in 12 minutes") == "sent"

    # The cap bounds volume, which the backoff does not: spacing alone lets a
    # long evening carry any number of them.
    a2.urgent_now = lambda: None
    # Unprompted sends only: no message_in between them, or they are replies
    # and replies are not rationed. Bounded so a counting bug cannot hang here.
    t3 = Transcript(Path(tmp) / "cap.db")
    a4 = Agent(cfg, t3, store, Tools(t3), "SYS")
    a4.deliver = lambda text: []
    a4._background, a4._went_quiet, a4._spoke_this_turn = True, False, False
    for _ in range(DAILY_UNPROMPTED_CAP):
        t3.append("message_out", text="unprompted")
    assert "cap" in a4.say("one more"), "the cap bounds volume, not just spacing"

    # A reply does not spend the budget: an evening of conversation used to
    # exhaust it and silence every proactive message for the rest of the night.
    t4 = Transcript(Path(tmp) / "replies.db")
    a5 = Agent(cfg, t4, store, Tools(t4), "SYS")
    a5.deliver = lambda text: []
    a5._background, a5._went_quiet, a5._spoke_this_turn = True, False, False
    for _ in range(DAILY_UNPROMPTED_CAP * 3):
        t4.append("message_in", text="and?")
        t4.append("message_out", text="answer")
    a5._spoke_this_turn = False
    a5.urgent_now = lambda: "event starting"   # past the spacing rule only
    assert a5.say("your 19:00 starts in 12 minutes") == "sent", \
        "conversation must not buy silence"

    # A deadline still gets through the cap — a missed event is not what the
    # cap protects him from.
    a2.urgent_now = lambda: "Robotics starts in 8 minutes"
    a2._spoke_this_turn = False
    assert a2.say("robotics in 8") == "sent"

    # Going quiet is the action; announcing it is not going quiet.
    a2._went_quiet = True
    assert a2.say("standing down for tonight").startswith("Error")

    # One message per turn: finishing one task sent two notifications.
    t3 = Transcript(Path(tmp) / "once.db")
    a3 = Agent(cfg, t3, store, Tools(t3), "SYS")
    a3.deliver = lambda text: []
    a3._background, a3._went_quiet, a3._spoke_this_turn = False, False, False
    assert a3.say("Marked InQuizitive Ch 6 done.") == "sent"
    assert a3.say("Nice — I marked it done and added a note.").startswith("Error")

    # He wants a routine, not an interview. These are his real messages.
    assert asks_a_question("Niranjan — want the after-school brief now?")
    assert asks_a_question("Quick brief or full todo list?")
    assert asks_a_question("Do you mean 8:30 PM tonight, and for how many minutes?")
    assert not asks_a_question("Two things due tonight.")
    # A question mark inside a title is not Argon asking anything.
    assert not asks_a_question('Read "Why We Crave Horror Movies?" is on the board.')

    t5 = Transcript(Path(tmp) / "asking.db")
    a6 = Agent(cfg, t5, store, Tools(t5), "SYS")
    a6.deliver = lambda text: []
    a6.urgent_now = lambda: "event starting"      # past the spacing rule

    # Unprompted, it may not ask him anything at all.
    a6._background, a6._went_quiet, a6._spoke_this_turn = True, False, False
    assert a6.say("want the after-school brief now?").startswith("Error")
    a6._spoke_this_turn = False
    assert a6.say("Two things due tonight.") == "sent"

    # Answering him, one question is fine; two in a row is the interview.
    t5.append("message_in", text="can you start blocking at 8:30")
    a6._background, a6._spoke_this_turn = False, False
    assert a6.say("Do you mean 8:30 tonight?") == "sent"
    a6._spoke_this_turn = False
    assert a6.say("And for how many minutes?").startswith("Error")

    # An invented absence is the error he cannot catch. These are the real
    # message from 20 Sep and the answers that must still get through.
    class _T:
        def __init__(self, title, subject, due):
            self.title, self.subject, self.due = title, subject, due

    apush = [_T("Reminder:  Inquizitive Ch 6 due tonight! ", "APUSH PM", "2026-09-20"),
             _T("Key Terms Chapter 7", "APUSH PM", "2026-09-23")]

    assert unmentioned_live_work(
        "APUSH has nothing due tomorrow — the only APUSH Classroom items are two "
        "past reminders (InQuizitive due 2026-09-07 and progress checks due "
        "2026-09-09)", apush), "the message that actually went out must be refused"

    assert not unmentioned_live_work(
        "APUSH open items: Inquizitive Ch 6 due today; Key Terms Chapter 7 due "
        "Wed 2026-09-23. No other APUSH tasks show.", apush), \
        "naming the work is fine however it is phrased"

    assert not unmentioned_live_work("Chemistry has nothing due this week.", apush), \
        "a class with no live work may be reported as empty"
    assert not unmentioned_live_work("Nothing else due tonight.", apush), \
        "a general absence naming no class is not judged here"
    assert not unmentioned_live_work("Key Terms Chapter 7 is due Wednesday.", apush)

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
