"""Turn the transcript into the message list sent to the model.

The whole cost model rests on one property: **everything before the tail is
byte-identical from one tick to the next.**  At $0.20/M fresh against $0.02/M
cached, a stable prefix is the difference between roughly $4 and roughly $11 a
month at a five-minute cadence.  ``_selftest`` asserts that property directly,
because it is the kind of thing that breaks silently and only shows up on a
bill.

Layout::

    [system]                      never changes within a day
    [sealed: everything up to and including the last thing Argon said]
    [tail:   what has happened since, plus the clock]

**The prefix is invalidated once a day, at midnight**, when ``window(days)``
drops the oldest day and every message shifts. That is one full-price call per
day — about a tenth of a cent — and it is deliberate: the alternative is
carrying a day he no longer cares about in order to keep a cache warm. The
first tick after midnight will cost roughly three times a warm one, which is
normal and not a sign the prefix is drifting. ``budget.cached_fraction`` is the
thing to watch; a single daily miss barely moves it.

The split is at the last spoken turn rather than at midnight because that is the
point after which content is still moving.  When Argon speaks, the old tail
collapses into the sealed prefix and the next tick caches it.

Two rendering decisions worth knowing:

*Ticks are not rendered individually.*  Ninety-six "a tick fired" lines a day is
noise that buries the signal.  They are counted instead, and the count appears
in the tail as "you have looked N times and stayed quiet" — which is the fact
that actually bears on whether to speak again.

*Observations are coalesced.*  A run of sensed events between two spoken turns
renders as one block, so a burst of phone-state changes does not fragment the
conversation into thirty one-line messages.
"""

from __future__ import annotations

from datetime import datetime

from argon import clock
from argon.transcript import SPOKEN, Event, Transcript

#: Rendered nowhere; counted in the tail.
COUNTED = {"tick"}


def _turn(event: Event) -> dict[str, str]:
    role = "user" if event.kind == "message_in" else "assistant"
    return {"role": role, "content": str(event.payload.get("text", ""))}


def _stamp(event: Event, today: str) -> str:
    """``23:54`` for today, ``Mon 23:54`` otherwise.

    The window is two days, so a bare HH:MM makes yesterday's 23:54 look later
    than today's 16:03 — the model was handed a list labelled "oldest first"
    that read as scrambled.
    """
    if event.day == today:
        return event.at[11:16]
    return f"{datetime.fromisoformat(event.at):%a} {event.at[11:16]}"


def _observation(events: list[Event]) -> dict[str, str]:
    """One block describing a run of things that happened."""
    lines = []
    today = clock.day_key()
    for e in events:
        at = _stamp(e, today)
        detail = e.payload.get("summary") or ", ".join(
            f"{k}={v}" for k, v in e.payload.items() if k != "summary"
        )
        lines.append(f"[{at}] {e.kind}{': ' + detail if detail else ''}")
    return {"role": "user", "content": "<observed>\n" + "\n".join(lines) + "\n</observed>"}


def _render(events: list[Event]) -> list[dict[str, str]]:
    """Spoken turns in order, with runs of observations coalesced between them."""
    out: list[dict[str, str]] = []
    pending: list[Event] = []
    for e in events:
        if e.kind in COUNTED:
            continue
        if e.kind in SPOKEN:
            if pending:
                out.append(_observation(pending))
                pending = []
            out.append(_turn(e))
        else:
            pending.append(e)
    if pending:
        out.append(_observation(pending))
    return out


def _split(events: list[Event]) -> tuple[list[Event], list[Event]]:
    """Everything through the last spoken turn, and everything after it."""
    cut = -1
    for i, e in enumerate(events):
        if e.kind in SPOKEN:
            cut = i
    return events[: cut + 1], events[cut + 1 :]


#: How many of its own recent messages the tail quotes back. One was not
#: enough: it said "standing down" four times in two hours, each time with the
#: previous one sitting in context but not in front of it.
RECENT_SAID = 4


def _tail(sealed: list[Event], loose: list[Event], extra: str = "") -> dict[str, str]:
    """The clock and the state of play — the only part that moves each tick.

    This is what replaces the occasion table.  Every fact a gate used to encode
    is stated here as information, and the model decides what it means.
    """
    now = clock.now()
    lines = [f"It is now {now.strftime('%A %d %b, %H:%M')}."]

    spoke = next((e for e in reversed(sealed) if e.kind == "message_out"), None)
    heard = next((e for e in reversed((*sealed, *loose)) if e.kind == "message_in"), None)
    if spoke is None:
        lines.append("You have not said anything to him yet.")
    else:
        mins = int((now - datetime.fromisoformat(spoke.at)).total_seconds() // 60)
        ago = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
        today = clock.day_key()
        said = [e for e in (*sealed, *loose) if e.kind == "message_out"][-RECENT_SAID:]
        lines.append(f"You last spoke at {_stamp(spoke, today)} ({ago} ago). "
                     f"Your recent messages, oldest first:")
        for e in said:
            lines.append(f"  [{_stamp(e, today)}] \"{str(e.payload.get('text', ''))[:160]}\"")
        if len(said) > 1:
            lines.append("If your next message would restate any of those, do not send it.")
        # Anything of his after that message counts, whether it landed in the
        # sealed prefix or the tail. `_split` cuts after the last *spoken*
        # event and his reply is spoken, so when he answered last his message
        # sits in `sealed` and a tail-only check reported him silent — which is
        # exactly the input that produces a nudge at someone who did reply.
        spoke_seq = spoke.seq
        replied = any(e.kind == "message_in" and e.seq > spoke_seq
                      for e in (*sealed, *loose))
        lines.append("He has replied since." if replied else "He has not replied since.")
        # Counted, not cliffed. A `mins < 30` threshold produced repeats at 31
        # and 36 minutes — which is the shape a soft threshold always produces,
        # because the model waits it out. A count does not expire.
        today = clock.day_key()
        sent_today = sum(1 for e in (*sealed, *loose)
                         if e.kind == "message_out" and e.day == today)
        heard_today = sum(1 for e in (*sealed, *loose)
                          if e.kind == "message_in" and e.day == today)
        lines.append(f"Today you have sent him {sent_today} message(s); "
                     f"he has sent you {heard_today}.")
        if heard is not None:
            gap = int((now - datetime.fromisoformat(heard.at)).total_seconds() // 60)
            ago = f"{gap // 60}h {gap % 60}m" if gap >= 60 else f"{gap}m"
            lines.append(f"He last said, {ago} ago: "
                         f"\"{str(heard.payload.get('text', ''))[:160]}\"")
        if sent_today >= 3 and heard_today == 0:
            lines.append(
                "He has not answered any of them. Every further message today "
                "makes the next one less likely to be read. Stay quiet unless "
                "something has genuinely changed that he can act on now."
            )
        if not replied and str(spoke.payload.get("text", "")).rstrip().endswith("?"):
            # Silence is an answer. It asked "which meeting?" at 18:58, was not
            # answered, and asked again at 23:14 about a meeting that had
            # finished at 19:00. An unanswered question is closed, not pending.
            lines.append(
                f"Your last message was a question and he did not answer it. "
                f"That is his answer. Do not ask it again, do not rephrase it, "
                f"and do not raise the subject unprompted — if it still matters "
                f"he will bring it up."
            )

    looks = sum(1 for e in loose if e.kind in COUNTED)
    if looks:
        lines.append(f"You have looked {looks} time(s) since then and stayed quiet.")

    lines.append(
        "Say something only by calling the `say` tool. Plain text is thinking, "
        "not a message, and is never delivered."
    )
    if extra:
        lines.append("")
        lines.append(extra)
    return {"role": "user", "content": "\n".join(lines)}


def build(transcript: Transcript, system: str, *, days: int = 2,
          extra: str = "") -> list[dict[str, str]]:
    """The full message list for one turn.

    *extra* is live state — the board, standing facts, today's schedule — and
    goes in the tail rather than the system prompt on purpose.  Anything that
    can change mid-evening must sit after the cached prefix, or every edit to a
    task invalidates the whole window and the month costs several times what it
    should.  It is a few hundred fresh tokens; the prefix is fifteen thousand
    cached ones.
    """
    events = transcript.window(days)
    sealed, loose = _split(events)
    return [
        {"role": "system", "content": system},
        *_render(sealed),
        *_render(loose),
        _tail(sealed, loose, extra),
    ]


def _selftest() -> None:
    """Run with ``python -m argon.context``."""
    import tempfile
    from datetime import datetime, timedelta
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        t = Transcript(Path(tmp) / "t.db")
        base = datetime(2026, 9, 14, 18, 0, tzinfo=clock.TZ)

        clock.set_for_test(base)
        t.append("message_in", text="hey")
        clock.set_for_test(base + timedelta(minutes=5))
        t.append("message_out", text="tonight is tight")

        clock.set_for_test(base + timedelta(minutes=10))
        t.append("event", summary="phone unlocked")
        first = build(t, "SYS")

        # Ticks accumulate; the prefix must not move.
        for n in range(1, 13):
            clock.set_for_test(base + timedelta(minutes=10 + n * 5))
            t.append("tick")
        later = build(t, "SYS")

        assert first[:-1] == later[:-1], "prefix drifted — every tick would miss cache"
        assert len(first) == len(later), "tick rendering leaked into the prefix"

        # An unanswered question is closed, not pending.
        clock.set_for_test(base + timedelta(minutes=60))
        t.append("message_out", text="which meeting do you mean?")
        clock.set_for_test(base + timedelta(minutes=300))
        unanswered = build(t, "SYS")[-1]["content"]
        assert "That is his answer" in unanswered, unanswered
        clock.set_for_test(base + timedelta(minutes=90))

        # A reply that is the newest row must count as a reply.
        clock.set_for_test(base + timedelta(minutes=100))
        t.append("message_out", text="how is the pset going")
        clock.set_for_test(base + timedelta(minutes=140))
        t.append("message_in", text="nearly done")
        clock.set_for_test(base + timedelta(minutes=145))
        answered = build(t, "SYS")[-1]["content"]
        assert "He has replied since." in answered, answered

        # The tail carries the facts the gates used to encode.
        tail = later[-1]["content"]
        assert "18:05" in tail and "has not replied" in tail, tail

        # Yesterday must carry its day, or a 23:54 from last night reads as
        # later than a 16:03 from this afternoon in a list labelled oldest
        # first. Today stays bare so the common case is not noisy.
        yesterday = Event(seq=1, at="2026-09-13T23:54:00-07:00", day="2026-09-13",
                          kind="message_out", payload={})
        same_day = Event(seq=2, at="2026-09-14T16:03:00-07:00", day="2026-09-14",
                         kind="message_out", payload={})
        assert _stamp(yesterday, "2026-09-14") == "Sun 23:54"
        assert _stamp(same_day, "2026-09-14") == "16:03"
        assert "tonight is tight" in tail, "it must see what it actually said"
        assert "looked 12 time(s)" in tail, tail

        # Speaking collapses the tail into the prefix.
        clock.set_for_test(base + timedelta(minutes=90))
        t.append("message_out", text="still nothing started?")
        after = build(t, "SYS")
        assert len(after) > len(later), "a new turn should extend the prefix"
        assert after[-1]["content"].count("looked") == 0, "tick count should reset on speech"

        # Midnight drops the oldest day, so the prefix legitimately changes.
        # Asserted so the once-daily full-price call is a known cost rather
        # than a surprise on a bill.
        before_midnight = build(t, "SYS")
        clock.set_for_test(datetime(2026, 9, 15, 0, 5, tzinfo=clock.TZ))
        after_midnight = build(t, "SYS")
        assert after_midnight != before_midnight, "the window really does roll"
        assert len(after_midnight) <= len(before_midnight)
        clock.set_for_test(base + timedelta(minutes=90))

        # Observations coalesce rather than fragmenting.
        for i in range(5):
            t.append("event", summary=f"e{i}")
        blocks = [m for m in build(t, "SYS") if m["content"].startswith("<observed>")]
        assert len(blocks) == 2, [b["content"] for b in blocks]
        assert blocks[-1]["content"].count("\n") == 6, blocks[-1]["content"]

        clock.set_for_test(None)
    print("context selftest ok")


if __name__ == "__main__":
    _selftest()
