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

    def turn(self, *, background: bool) -> Outcome:
        """Run one turn to completion."""
        out = Outcome()
        messages = context.build(self.t, self.system)
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
                if reply.text and not background:
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
        """The only path to him.  Appends before sending so a send failure
        cannot make Argon forget it already spoke — repeating a message he did
        get is worse than dropping one he did not."""
        text = (text or "").strip()
        if not text:
            return "Error: empty message not sent"
        self.t.append("message_out", text=text)
        self.deliver(text)
        return "sent"

    #: Replaced by the runtime with the real channel. Default drops the message
    #: rather than crashing, so a misconfigured channel is visible in the
    #: transcript instead of taking the loop down.
    def deliver(self, text: str) -> None:  # pragma: no cover - overridden
        pass

    def receive(self, text: str, *, source: str = "ios") -> Outcome:
        """He said something.  Record it, then answer."""
        self.t.append("message_in", text=text, source=source)
        return self.turn(background=False)


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
        agent.deliver = delivered.append
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

        clock.set_for_test(None)
        del os.environ["ARGON_HOME"]
    print("agent selftest ok")


if __name__ == "__main__":
    _selftest()
