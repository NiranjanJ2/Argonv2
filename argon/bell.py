"""Whitney High School bell schedules.

Carried over verbatim from the old system because it is a durable fact about
the world, not code worth reinventing: the times are the school's, and they are
right.  Everything else in this rewrite starts empty.

Times are HHMM integers — 8:30 AM is 830, 1:30 PM is 1330 — which sorts and
compares correctly without parsing and is how the source data is published.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from argon import clock, config

Entry = tuple[str, int, int]  # (label, start HHMM, end HHMM)

#: Value in overrides.json meaning no school that day.
NO_SCHOOL = "none"

SCHEDULES: dict[str, list[Entry]] = {
    "regular": [
        ("Period 0", 730, 825), ("Period 1", 830, 928), ("Period 2", 933, 1030),
        ("Snack", 1030, 1045), ("Period 3", 1050, 1147), ("Period 4", 1152, 1249),
        ("Lunch", 1249, 1321), ("Period 5", 1326, 1423), ("HR", 1423, 1434),
        ("Period 6", 1439, 1536),
    ],
    "early_release": [
        ("Period 0", 730, 825), ("Period 1", 830, 921), ("Period 2", 926, 1016),
        ("Snack", 1016, 1031), ("Period 3", 1036, 1126), ("Period 4", 1131, 1221),
        ("Lunch", 1221, 1251), ("Period 5", 1256, 1346), ("Period 6", 1351, 1441),
        ("Meeting", 1450, 1550),
    ],
    "advisement": [
        ("Period 0", 730, 825), ("Period 1", 830, 923), ("Period 2", 928, 1020),
        ("Snack", 1020, 1035), ("Period 3", 1040, 1132), ("Period 4", 1137, 1229),
        ("Lunch", 1229, 1301), ("Period 5", 1306, 1358), ("Adv/HR", 1358, 1439),
        ("Period 6", 1444, 1536),
    ],
    "activity": [
        ("Period 0", 730, 825), ("Period 1", 830, 924), ("Period 2", 929, 1022),
        ("Snack", 1022, 1037), ("Period 3", 1042, 1135), ("Period 4", 1140, 1233),
        ("Activity", 1233, 1308), ("Lunch", 1308, 1340), ("Period 5", 1345, 1438),
        ("Period 6", 1443, 1536),
    ],
    "minimum_day": [
        ("Period 0", 750, 825), ("Period 1", 830, 907), ("Period 2", 912, 948),
        ("Period 3", 953, 1029), ("Snack", 1029, 1049), ("Period 4", 1054, 1130),
        ("Period 5", 1135, 1211), ("Period 6", 1216, 1252),
    ],
    "special_events": [
        ("Period 0", 730, 825), ("Period 1", 830, 921), ("Period 2", 926, 1016),
        ("Snack", 1016, 1031), ("Period 3", 1036, 1126), ("Period 4", 1131, 1221),
        ("Event", 1221, 1314), ("Lunch", 1314, 1346), ("Period 5", 1351, 1441),
        ("Period 6", 1446, 1536),
    ],
    "comp_1st_qtr": [
        ("Period 0", 730, 825), ("1st Comp", 830, 1033), ("Snack", 1033, 1048),
        ("2nd Comp", 1053, 1256), ("Lunch", 1256, 1328), ("3rd Comp", 1333, 1536),
    ],
    "comp_semester": [
        ("Period 0", 730, 825), ("1st Comp", 830, 1030), ("Snack", 1030, 1055),
        ("2nd Comp", 1100, 1300),
    ],
    "first_day": [
        ("Period 0", 730, 825), ("Rally", 830, 900), ("Schedule", 900, 915),
        ("Period 1", 920, 1006), ("Period 2", 1011, 1057), ("Snack", 1057, 1117),
        ("Period 3", 1122, 1208), ("Period 4", 1213, 1259), ("Lunch", 1259, 1339),
        ("Period 5", 1344, 1430), ("Period 6", 1435, 1521),
    ],
}

#: Monday=0. Friday is often Activity and needs an override when it is.
DEFAULT_BY_WEEKDAY: dict[int, str] = {
    0: "regular", 1: "early_release", 2: "advisement", 3: "regular", 4: "regular",
}

DISPLAY: dict[str, str] = {
    "regular": "Regular (M/Th/F)", "early_release": "Early Release (Tuesday)",
    "advisement": "Advisement (Wednesday)", "activity": "Activity Friday",
    "minimum_day": "Minimum Day", "special_events": "Special Events",
    "comp_1st_qtr": "1st Quarter Comps", "comp_semester": "Semester / 3rd Qtr Comps",
    "first_day": "First Day of School",
}


def _overrides() -> dict[str, str]:
    p = config.home() / "overrides.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except json.JSONDecodeError:
        return {}  # a typo in a hand-edited file must not stop the server


def schedule_for(day: date) -> str | None:
    """Schedule name for *day*, or None when there is no school.

    ponytail: weekday plus a hand-written override file, no district calendar.
    It will be wrong over winter break unless he writes the override. Wire in
    the real calendar when a wrong December morning actually costs something.
    """
    override = _overrides().get(day.strftime("%Y-%m-%d"))
    if override == NO_SCHOOL:
        return None
    if override in SCHEDULES:
        return override
    if day.weekday() > 4:
        return None
    return DEFAULT_BY_WEEKDAY.get(day.weekday())


def periods(day: date) -> list[Entry]:
    name = schedule_for(day)
    return SCHEDULES.get(name, []) if name else []


def current_period(when: datetime | None = None) -> str | None:
    """The period he is in right now, or None outside school."""
    when = when or clock.now()
    hhmm = when.hour * 100 + when.minute
    for label, start, end in periods(when.date()):
        if start <= hhmm < end:
            return label
    return None


def describe(day: date | None = None) -> str:
    """One line for the prompt: today's schedule and when school ends."""
    day = day or clock.today()
    name = schedule_for(day)
    if not name:
        return "No school today."
    entries = SCHEDULES[name]
    end = entries[-1][2]
    return f"{DISPLAY.get(name, name)}; school ends {end // 100}:{end % 100:02d}."


def _selftest() -> None:
    from datetime import datetime as dt

    monday, tuesday = date(2026, 9, 14), date(2026, 9, 15)
    saturday = date(2026, 9, 19)
    assert monday.weekday() == 0 and saturday.weekday() == 5

    assert schedule_for(monday) == "regular"
    assert schedule_for(tuesday) == "early_release"
    assert schedule_for(saturday) is None, "no school at the weekend"
    assert periods(saturday) == []

    assert current_period(dt(2026, 9, 14, 8, 45, tzinfo=clock.TZ)) == "Period 1"
    assert current_period(dt(2026, 9, 14, 13, 0, tzinfo=clock.TZ)) == "Lunch"
    assert current_period(dt(2026, 9, 14, 18, 0, tzinfo=clock.TZ)) is None
    # A gap between periods is not a period.
    assert current_period(dt(2026, 9, 14, 9, 30, tzinfo=clock.TZ)) is None

    assert "ends 15:36" in describe(monday), describe(monday)
    assert describe(saturday) == "No school today."

    # Every schedule is ordered and non-overlapping, or current_period lies.
    for name, entries in SCHEDULES.items():
        for (_, _, end), (_, nxt, _) in zip(entries, entries[1:]):
            assert end <= nxt, f"{name} overlaps at {end}"
    print("bell selftest ok")


if __name__ == "__main__":
    _selftest()
