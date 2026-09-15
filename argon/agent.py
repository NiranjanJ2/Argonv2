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

from dataclasses import dataclass, field

from argon import budget, context, provider
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
        text = (text or "").strip()
        if not text:
            return "Error: empty message not sent"

        errors = self.deliver(text)
        if errors:
            self.t.append("undelivered", text=text, summary="; ".join(errors)[:200])
            return (f"NOT DELIVERED — {'; '.join(errors)[:200]}. He did not receive this. "
                    f"The channel is misconfigured; rewording will not help. "
                    f"Do not call say again this turn.")
        self.t.append("message_out", text=text)
        return "sent"

    #: Replaced by the runtime with the real channels. Returns the delivery
    #: failures; empty means it landed. The default accepts, so a bare Agent in
    #: a test is not reporting a broken channel.
    def deliver(self, text: str) -> list[str]:  # pragma: no cover - overridden
        return []

    def receive(self, text: str, *, source: str = "ios", extra: str = "") -> Outcome:
        """He said something.  Record it, then answer."""
        self.t.append("message_in", text=text, source=source)
        return self.turn(background=False, extra=extra)


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
    print("agent selftest ok")


if __name__ == "__main__":
    _selftest()
