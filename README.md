# Argon v2

A personal assistant that decides, rather than a state machine that phrases.

## Why this exists

v1 worked, in the sense that it ran. But every time the model did something
wrong, the fix took authority away from it. That ratchet only turns one way, and
by the end the function that decided whether to speak began:

```python
def pick_occasion(self) -> Occasion | None:
    """The reason to reach out right now, or None. No model call involved."""
```

Three hardcoded occasions survived, all time-triggered. The model was woken
afterward to phrase a verdict Python had already reached. That is a template
engine behind a scheduler, and it took 1,089 lines to decide whether to send one
message.

The diagnosis in v1's own notes was *"every guard in this codebase exists
because a model judgment failed."* Reading the post-mortems, that is not what
happened:

- A 45-minute check-in that had *"nothing to say"* — it was handed nothing new.
  **Context problem.**
- Announcing work had started while he was asleep — it didn't know he was
  asleep. **Sensing problem.**
- Thirteen identical nudges in eight days — it couldn't see it had already
  asked twelve times; dedup ran *outside* the model. **Memory problem.**

Every one is missing information, not bad judgment. v2 gives the model what a
human assistant would have and lets it decide.

## The shape

**One append-only transcript is the entire state.** Messages in and out, tool
calls, sensed events, ticks — one row each. The agent's context is literally the
last two days of it, rendered in order.

Three things follow, and they are the whole design:

**The prompt cache works by construction.** Rows are immutable and only ever
appended, so everything before the last spoken turn is byte-identical from one
tick to the next. Cached input is $0.02/M against $0.20/M fresh; at a
five-minute cadence that is ~$3/month instead of ~$11. `context.py`'s self-check
asserts the prefix does not drift, because it breaks silently and you find out
on a bill.

The prefix is invalidated once a day at midnight, when the two-day window drops
its oldest day. That is one full-price call per day, on purpose — keeping the
cache warm is not worth carrying a day he has stopped caring about.

**Restraint is information, not a gate.** The agent reads that it spoke at 18:05
and has not been answered. `max_per_day` is gone. So is the occasion table.

**It replays.** "Why did it say that" is one table read, not an excavation
across DailyState, the board, the daily log and phone state — any of which could
disagree.

### The rule that does the safety work

**A tick's plain text is thinking and is discarded. Only `say` reaches him.**

Interactive turns differ in exactly one way: he asked and is waiting, so the
reply text is delivered. On an unprompted turn there is no path from "the model
produced characters" to "his phone buzzed". That kills two historical bugs
structurally rather than by guarding: a rambling tick reaches nobody, and a
provider error can never arrive as a 4 PM brief, because an exception is not a
tool call.

### What Python still decides

Only cost. `schedule.py` answers "is this moment worth a model call" — 16:00 to
midnight, school nights, Friday 16:00 through Sunday noon off. It never decides
whether something is worth *saying*. An inbound message wakes Argon regardless.

## Layout

```
argon/
  clock.py        one source of now, always tz-aware
  transcript.py   the append-only log that is the state
  context.py      transcript -> messages, cache-shaped
  schedule.py     when to look (cost only, never content)
  agent.py        the turn: build, ask, run tools
  provider.py     OpenAI-compatible, stdlib, raises instead of returning errors
  budget.py       hard monthly ceiling, announced once
  store.py        tasks and durable facts; every mutation is also an event
  tools.py        the registry, and `say`
  bell.py         Whitney schedules (the one thing carried from v1)
  runtime.py      the object graph and the tick loop
  api.py          frozen /v1 for the current app, clean /v2 for the new one
  channels.py     Discord
  integrations/   google, push (APNs), ac (Gree)
  prompts/argon.md
ios/Argon/        the Argon layer; Foqos keeps its own screens
desktop/          SwiftBar + Übersicht, one script behind both
```

No framework. The agent core imports nothing outside the standard library —
`urllib`, `json`, `sqlite3`, `dataclasses`. Frameworks want to own message
assembly, and message assembly is the cost model.

## Running it

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
cp config.sample.json ~/.argon2/config.json    # then edit it
.venv/bin/argon doctor                          # checks what has broken before
.venv/bin/argon tick                            # one turn now, prints what it would do
.venv/bin/argon gateway                         # the real thing
```

`./check.sh` runs every self-check — Python, the desktop script, and a Swift
typecheck. No pytest: each module proves itself with asserts under
`python -m argon.<module>`.

State lives in `~/.argon2` (`ARGON_HOME` moves it). The checkout holds no data
and the data root holds nothing executable.

## Things that have already cost a wrong diagnosis

- **Google refresh tokens die every 7 days** while the OAuth consent screen is
  in *Testing*. Google policy; no code fixes it. Publish the app. If every
  account fails `invalid_grant` at once, that is what happened.
- **An APNs key scoped to Development at creation can never be widened.**
  Production then fails on auth before reading the device token, which looks
  exactly like a dead registration. `argon doctor` probes both environments
  against a fake token.
- **Don't pin a background model to a different provider than chat.** One was
  retired, chat kept working on the other, and every check-in returned 410 for
  a week with nothing looking broken.
- **`Timer.scheduledTimer` is suspended when iOS backgrounds the app.** Anything
  that must happen with the app closed belongs in a `DeviceActivitySchedule`.
- **`budget.cached_fraction()` is the canary.** Below ~0.5 with real traffic
  means the context prefix is drifting and the month is about to cost several
  times what it should.
