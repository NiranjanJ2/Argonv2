"""When Argon looks on its own.

This module decides *cost*, never *content*.  It answers "is it worth spending a
model call to look right now" and nothing else.  What to do once awake is the
agent's call, always — the moment a schedule starts deciding whether something
is worth mentioning, it has become an occasion table again, which is the thing
this rewrite exists to remove.

**An inbound message wakes Argon regardless of anything here.**  The lull below
suppresses the proactive timer only.  He can text at noon on Saturday and get an
answer; Argon simply will not be watching for him.
"""

from __future__ import annotations

from datetime import datetime

from argon import clock

#: Proactive looking runs from this hour to midnight.  He is home from school
#: around four, naps, and aims to start by eight; looking earlier watches an
#: empty room.
WINDOW_START_HOUR = 16

#: Minutes between looks inside the window.  Short on purpose: at this cadence
#: the prompt cache stays warm between ticks, and a warm tick costs about a
#: tenth of a cold one.  A *longer* interval can therefore cost more per month
#: than a shorter one — measure before widening this.
TICK_MINUTES = 5


def in_weekend_lull(when: datetime) -> bool:
    """Friday 16:00 through Sunday 12:00 — his weekend.

    Stated as one span because that is how he described it, and because the
    Sunday clause stays correct if ``WINDOW_START_HOUR`` ever moves earlier.
    Sunday evening is a school night and is deliberately not in here.
    """
    weekday, hour = when.weekday(), when.hour
    if weekday == 4 and hour >= WINDOW_START_HOUR:  # Friday, once school is out
        return True
    if weekday == 5:  # Saturday, all of it
        return True
    if weekday == 6 and hour < 12:  # Sunday morning
        return True
    return False


def should_tick(when: datetime | None = None) -> bool:
    """Should the proactive timer wake the agent now?

    ponytail: weekday arithmetic, no school calendar — this will be wrong on
    winter break and holidays.  Wire in the district calendar when a wrong
    December evening actually costs something.
    """
    when = when or clock.now()
    if in_weekend_lull(when):
        return False
    return when.hour >= WINDOW_START_HOUR


def _selftest() -> None:
    """Run with ``python -m argon.schedule``."""

    def at(day: int, hour: int) -> datetime:
        # 2026-09-14 is a Monday; day 0 lands on it.
        return datetime(2026, 9, 14 + day, hour, 0, tzinfo=clock.TZ)

    # School nights: Monday through Thursday evenings are on.
    for day in range(4):
        assert should_tick(at(day, 16)), f"Mon+{day} 16:00 should tick"
        assert should_tick(at(day, 23)), f"Mon+{day} 23:00 should tick"
        assert not should_tick(at(day, 15)), "before the window"
        assert not should_tick(at(day, 9)), "school hours"

    assert at(4, 18).weekday() == 4 and at(5, 12).weekday() == 5
    assert not should_tick(at(4, 18)), "Friday night is his"
    assert not should_tick(at(4, 23)), "Friday night is his"
    assert should_tick(at(4, 15)) is False, "outside the window anyway"
    assert not should_tick(at(5, 12)), "Saturday is his"
    assert not should_tick(at(5, 20)), "Saturday evening is his"

    # Sunday: morning off, evening back on because Monday is a school day.
    assert at(6, 10).weekday() == 6
    assert not should_tick(at(6, 10)), "Sunday morning is his"
    assert in_weekend_lull(at(6, 10)) and not in_weekend_lull(at(6, 13))
    assert should_tick(at(6, 17)), "Sunday evening is a school night"

    # The lull is one contiguous span from Friday 16:00 to Sunday 12:00.
    assert not in_weekend_lull(at(4, 15)), "Friday afternoon is not yet the lull"
    assert in_weekend_lull(at(4, 16)) and in_weekend_lull(at(5, 3))
    print("schedule selftest ok")


if __name__ == "__main__":
    _selftest()
