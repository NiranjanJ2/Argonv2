"""The only source of "now".

Everything that needs the time goes through here so tests can move it and so a
timezone is never implied by the machine's locale.  The old codebase called
``datetime.now()`` in thirty places and two of them were naive, which is how a
block boundary fired against UTC while he was asleep.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")

#: Swapped by tests. Production never assigns to this.
_override: datetime | None = None


def now() -> datetime:
    """Local time, always tz-aware."""
    return _override or datetime.now(TZ)


def today() -> date:
    return now().date()


def day_key(when: datetime | None = None) -> str:
    """The local ``YYYY-MM-DD`` a moment belongs to.

    Stored on every transcript row so the two-day window is an index lookup
    instead of timezone arithmetic inside SQL.
    """
    return (when or now()).astimezone(TZ).strftime("%Y-%m-%d")


def recent_days(count: int, when: datetime | None = None) -> list[str]:
    """Day keys, oldest first: ``recent_days(2)`` is yesterday then today.

    Oldest first because the context prefix is built in this order and must stay
    byte-stable as the day advances.
    """
    end = (when or now()).astimezone(TZ).date()
    return [(end - timedelta(days=n)).strftime("%Y-%m-%d") for n in reversed(range(count))]


def set_for_test(value: datetime | None) -> None:
    global _override
    _override = value
