"""A hard monthly ceiling on model spend.

He chose a hard stop over graceful degradation: at the cap Argon refuses to
call the model and tells him once.  That is a real tradeoff and worth stating
plainly — spend becomes predictable and *availability* becomes unpredictable.
At roughly $3 projected against a $5 cap there is headroom, but a heavy month
or a run of cache misses can still end the month early, and the failure is
silent in effect: one message, then nothing.  ``notified_this_month`` exists so
it is at least not silent in fact.

Cached input is an order of magnitude cheaper than fresh, which is why the
transcript is append-only and the context prefix never moves.  ``record``
tracks the two separately so the cache can be *seen* to be working rather than
assumed: if ``month()['cached_frac']`` drops, the prefix has started drifting
and the bill is about to triple.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from argon import clock, config

#: dollars per million tokens: (input, output, cached input)
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5.6-luna": (0.20, 1.20, 0.02),
    "gpt-5.6-terra": (1.25, 10.00, 0.125),
    "gpt-5.6-sol": (5.00, 40.00, 0.50),
    "gpt-5-mini": (0.25, 2.00, 0.025),
}

#: Warn in the log once past this fraction of the cap.
WARN_AT = 0.80


class BudgetExceeded(RuntimeError):
    """Raised instead of spending past the cap."""


@dataclass
class Usage:
    prompt: int = 0
    completion: int = 0
    cached: int = 0


def price_of(model: str) -> tuple[float, float, float] | None:
    """Rates for *model*, or None when it is unmetered.

    An unmetered model costs nothing and is never blocked — better to under-bill
    an unknown model than to refuse every call because a name is missing here.
    """
    return PRICES.get((model or "").split("/")[-1].strip())


def cost_of(model: str, usage: Usage) -> float:
    rates = price_of(model)
    if rates is None:
        return 0.0
    fresh_in, out, cached_in = rates
    fresh = max(0, usage.prompt - usage.cached)
    return (
        fresh * fresh_in / 1e6
        + usage.cached * cached_in / 1e6
        + usage.completion * out / 1e6
    )


def _file():
    return config.path("spend.json")


def _read() -> dict:
    p = _file()
    return json.loads(p.read_text()) if p.exists() else {}


def _write(data: dict) -> None:
    _file().write_text(json.dumps(data, indent=2, sort_keys=True))


def month() -> dict:
    """This month's totals."""
    key = clock.now().strftime("%Y-%m")
    return _read().get(key) or {"usd": 0.0, "calls": 0, "prompt": 0, "cached": 0,
                                "notified": False}


def cached_fraction() -> float:
    """Share of prompt tokens served from cache.  The cost model lives here.

    Expect this above ~0.7 at a five-minute cadence.  Materially lower means
    the context prefix is being rebuilt and the month will cost several times
    what it should.
    """
    m = month()
    return (m["cached"] / m["prompt"]) if m["prompt"] else 0.0


def remaining(cap: float) -> float:
    return max(0.0, cap - month()["usd"])


def check(cap: float, model: str) -> None:
    """Raise ``BudgetExceeded`` if this call must not happen."""
    if price_of(model) is None:
        return  # unmetered: never blocked
    spent = month()["usd"]
    if spent >= cap:
        raise BudgetExceeded(f"monthly cap reached (${spent:.2f} of ${cap:.2f})")


def record(model: str, usage: Usage) -> float:
    """Add one call to the month.  Returns its cost."""
    cost = cost_of(model, usage)
    key = clock.now().strftime("%Y-%m")
    data = _read()
    m = data.setdefault(key, {"usd": 0.0, "calls": 0, "prompt": 0, "cached": 0,
                              "notified": False})
    m["usd"] = round(m["usd"] + cost, 6)
    m["calls"] += 1
    m["prompt"] += usage.prompt
    m["cached"] += usage.cached
    _write(data)
    return cost


def take_notification(cap: float) -> str | None:
    """The one message to send when the cap is hit, or None.

    Returns text exactly once per month.  Ninety-six ticks a day would
    otherwise each announce the same thing, which is the spam failure this
    whole rewrite is meant to end — including when the spammer is the budget.
    """
    key = clock.now().strftime("%Y-%m")
    data = _read()
    m = data.get(key)
    if not m or m["usd"] < cap or m.get("notified"):
        return None
    m["notified"] = True
    _write(data)
    return (
        f"I've hit the ${cap:.0f} model budget for this month (${m['usd']:.2f} over "
        f"{m['calls']} calls). I'm going quiet until the 1st — text me and I still "
        f"won't answer. Raise the cap in config.json if you want me back."
    )


def _selftest() -> None:
    import os
    import tempfile
    from datetime import datetime

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ARGON_HOME"] = tmp
        clock.set_for_test(datetime(2026, 9, 14, 18, 0, tzinfo=clock.TZ))

        # Cached input is 10x cheaper; that ratio is the whole cost model.
        fresh = cost_of("gpt-5.6-luna", Usage(prompt=18_000, completion=150))
        warm = cost_of("gpt-5.6-luna", Usage(prompt=18_000, completion=150, cached=13_000))
        assert abs(fresh - 0.00378) < 1e-5, fresh
        assert warm < fresh / 2, (warm, fresh)

        assert cost_of("who-knows", Usage(prompt=10**9)) == 0.0, "unmetered is free"
        check(5.0, "who-knows")  # and never blocked

        for _ in range(10):
            record("gpt-5.6-luna", Usage(prompt=18_000, completion=150, cached=13_000))
        m = month()
        assert m["calls"] == 10 and m["usd"] > 0
        assert abs(cached_fraction() - 13 / 18) < 0.01, cached_fraction()

        check(5.0, "gpt-5.6-luna")  # nowhere near the cap
        assert take_notification(5.0) is None, "no notice below the cap"

        # Drive it over and confirm the stop is hard and announced exactly once.
        record("gpt-5.6-sol", Usage(prompt=2_000_000, completion=0))
        try:
            check(5.0, "gpt-5.6-luna")
            raise AssertionError("should have refused")
        except BudgetExceeded:
            pass
        assert take_notification(5.0) is not None, "must announce once"
        assert take_notification(5.0) is None, "must not announce twice"
        assert remaining(5.0) == 0.0

        # A new month starts clean.
        clock.set_for_test(datetime(2026, 10, 1, 9, 0, tzinfo=clock.TZ))
        assert month()["usd"] == 0.0 and month()["notified"] is False
        check(5.0, "gpt-5.6-luna")

        clock.set_for_test(None)
        del os.environ["ARGON_HOME"]
    print("budget selftest ok")


if __name__ == "__main__":
    _selftest()
