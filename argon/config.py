"""Where state lives and what the operator may set.

Code and state are separate: the checkout holds no data and ``~/.argon2`` holds
nothing executable.  ``ARGON_HOME`` moves the data root, which is how tests get
a scratch one and how a second instance can run beside the live one.

Deliberately small.  The previous config had twenty-four provider entries and a
keyword auto-detector; every endpoint here is OpenAI-compatible and the model
string is just a string.  Nothing in this file is clever, which is the point —
a setting that needs explaining belongs in code with a comment, not in JSON.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints


def home() -> Path:
    """The data root.  Everything mutable lives under here."""
    return Path(os.environ.get("ARGON_HOME") or Path.home() / ".argon2")


def path(*parts: str) -> Path:
    p = home().joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Provider:
    """One OpenAI-compatible endpoint."""

    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-5.6-luna"
    #: Used when the primary returns a retryable failure.  Keep it on the same
    #: provider: pinning a background model to a *different* one is what caused
    #: a week-long silent outage when that model was retired and chat kept
    #: working, so nothing looked broken.
    fallback_model: str = "gpt-5-mini"
    timeout_s: float = 90.0


@dataclass
class Discord:
    enabled: bool = False
    token: str = ""
    #: Only these user ids may talk to Argon.  Empty means nobody.
    allow_from: list[str] = field(default_factory=list)
    #: Where to deliver when he has not spoken yet.  Without this a fresh
    #: install has nowhere to send the first brief, and it goes nowhere
    #: silently — which is how two days of check-ins were lost once before.
    channel_id: str = ""


@dataclass
class Apns:
    enabled: bool = False
    team_id: str = ""
    #: The .p8 is read from ``~/.argon2/apns/AuthKey_<key_id>.p8``.  An APNs key
    #: scoped to Development at creation can never be widened, and production
    #: then fails auth before it ever looks at the device token — which reads
    #: exactly like a dead token.  Probe both environments before believing one.
    key_id: str = ""
    bundle_id: str = "com.niranjanj.argon"
    #: False targets api.sandbox.push.apple.com.
    production: bool = True


@dataclass
class Api:
    host: str = "0.0.0.0"
    port: int = 3995
    #: Bearer token for every route except /health.  Empty disables the server
    #: rather than serving it open.
    token: str = ""


@dataclass
class Config:
    provider: Provider = field(default_factory=Provider)
    discord: Discord = field(default_factory=Discord)
    apns: Apns = field(default_factory=Apns)
    api: Api = field(default_factory=Api)
    #: Hard monthly ceiling in dollars.  See budget.py for what happens at it.
    monthly_cap_usd: float = 5.00
    #: Google account names to keep authorised, e.g. ["personal", "school"].
    google_accounts: list[str] = field(default_factory=list)


def _build(cls: type, data: dict[str, Any]) -> Any:
    """Construct *cls* from *data*, ignoring keys it does not declare.

    Unknown keys are dropped rather than raising: a config written by a newer
    build should not stop an older one from starting.
    """
    # get_type_hints, not f.type: `from __future__ import annotations` makes
    # every annotation a string, so is_dataclass(f.type) is always False and
    # nested sections stay raw dicts.  The selftest below catches that.
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value, hint = data[f.name], hints[f.name]
        kwargs[f.name] = _build(hint, value) if is_dataclass(hint) and isinstance(value, dict) else value
    return cls(**kwargs)


def load() -> Config:
    """Read ``~/.argon2/config.json``, falling back to defaults throughout."""
    p = home() / "config.json"
    if not p.exists():
        return Config()
    return _build(Config, json.loads(p.read_text()))


def _selftest() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ARGON_HOME"] = tmp
        assert home() == Path(tmp)
        assert load().monthly_cap_usd == 5.00, "defaults when no file"

        (Path(tmp) / "config.json").write_text(json.dumps({
            "provider": {"api_key": "k", "model": "m"},
            "monthly_cap_usd": 12.0,
            "unknown_future_key": {"a": 1},
        }))
        c = load()
        assert c.provider.api_key == "k" and c.provider.model == "m"
        assert c.provider.fallback_model == "gpt-5-mini", "unset fields keep defaults"
        assert c.monthly_cap_usd == 12.0
        assert c.discord.enabled is False and c.discord.allow_from == []
        assert path("a", "b.json").parent.exists(), "path() makes parents"
        del os.environ["ARGON_HOME"]
    print("config selftest ok")


if __name__ == "__main__":
    _selftest()
