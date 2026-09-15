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


def _observation(events: list[Event]) -> dict[str, str]:
    """One block describing a run of things that happened."""
    lines = []
    for e in events:
        at = e.at[11:16]
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


def _tail(sealed: list[Event], loose: list[Event]) -> dict[str, str]:
    """The clock and the state of play — the only part that moves each tick.

    This is what replaces the occasion table.  Every fact a gate used to encode
    is stated here as information, and the model decides what it means.
    """
    now = clock.now()
    lines = [f"It is now {now.strftime('%A %d %b, %H:%M')}."]

    spoke = next((e for e in reversed(sealed) if e.kind == "message_out"), None)
    if spoke is None:
        lines.append("You have not said anything to him yet.")
    else:
        mins = int((now - datetime.fromisoformat(spoke.at)).total_seconds() // 60)
        ago = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
        lines.append(f"You last spoke at {spoke.at[11:16]} ({ago} ago).")
        replied = any(e.kind == "message_in" for e in loose)
        lines.append("He has replied since." if replied else "He has not replied since.")

    looks = sum(1 for e in loose if e.kind in COUNTED)
    if looks:
        lines.append(f"You have looked {looks} time(s) since then and stayed quiet.")

    lines.append(
        "Say something only by calling the `say` tool. Plain text is thinking, "
        "not a message, and is never delivered."
    )
    return {"role": "user", "content": "\n".join(lines)}


def build(transcript: Transcript, system: str, *, days: int = 2) -> list[dict[str, str]]:
    """The full message list for one turn."""
    events = transcript.window(days)
    sealed, loose = _split(events)
    return [
        {"role": "system", "content": system},
        *_render(sealed),
        *_render(loose),
        _tail(sealed, loose),
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

        # The tail carries the facts the gates used to encode.
        tail = later[-1]["content"]
        assert "18:05" in tail and "has not replied" in tail, tail
        assert "looked 12 time(s)" in tail, tail

        # Speaking collapses the tail into the prefix.
        clock.set_for_test(base + timedelta(minutes=90))
        t.append("message_out", text="still nothing started?")
        after = build(t, "SYS")
        assert len(after) > len(later), "a new turn should extend the prefix"
        assert after[-1]["content"].count("looked") == 0, "tick count should reset on speech"

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
