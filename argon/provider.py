"""The model call.  OpenAI-compatible, stdlib only.

**Errors raise; they never return text.**  The old ``extract_message`` passed
whatever came back straight through, which is how ``Error calling LLM: Error
code: 504`` was delivered as a 4 PM brief.  Nothing here can produce a string
that reaches him: a failure is an exception, and the only path to his phone is
an explicit ``say`` tool call.

ponytail: urllib, not httpx.  This is one POST with a JSON body; a dependency
would buy connection pooling Argon has no use for at one call per five minutes.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from argon import budget
from argon.config import Provider

#: Status codes worth trying again.  A 400 means the request is wrong and will
#: be wrong next time; retrying it just burns the clock.
RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    """The model could not be reached or refused the request."""


@dataclass
class Reply:
    """One assistant turn."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    usage: budget.Usage = field(default_factory=budget.Usage)
    cost: float = 0.0


def _post(url: str, key: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        raise ProviderError(f"HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError(f"unreachable: {e}") from e


def _retryable(exc: ProviderError) -> bool:
    text = str(exc)
    if text.startswith("unreachable"):
        return True
    code = text[5:8] if text.startswith("HTTP ") else ""
    return code.isdigit() and int(code) in RETRYABLE


def _usage(raw: dict) -> budget.Usage:
    u = raw.get("usage") or {}
    details = u.get("prompt_tokens_details") or {}
    return budget.Usage(
        prompt=int(u.get("prompt_tokens") or 0),
        completion=int(u.get("completion_tokens") or 0),
        cached=int(details.get("cached_tokens") or 0),
    )


def complete(
    cfg: Provider,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    cap: float,
    attempts: int = 3,
) -> Reply:
    """One turn.  Raises ``ProviderError`` or ``BudgetExceeded``; never both.

    The fallback model is tried only after the primary exhausts its retries, and
    it lives on the *same* provider on purpose: a background model pinned to a
    different one silently 410'd for a week while chat kept working on another,
    so nothing looked broken.
    """
    url = cfg.api_base.rstrip("/") + "/chat/completions"
    last: ProviderError | None = None

    for model in (cfg.model, cfg.fallback_model):
        if not model:
            continue
        budget.check(cap, model)  # refuses before spending, not after
        body: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        for attempt in range(attempts):
            try:
                raw = _post(url, cfg.api_key, body, cfg.timeout_s)
            except ProviderError as e:
                last = e
                if not _retryable(e) or attempt == attempts - 1:
                    break
                time.sleep(2**attempt)  # 1s, 2s
                continue

            choice = (raw.get("choices") or [{}])[0].get("message") or {}
            usage = _usage(raw)
            return Reply(
                text=choice.get("content") or "",
                tool_calls=choice.get("tool_calls") or [],
                model=model,
                usage=usage,
                cost=budget.record(model, usage),
            )

    raise ProviderError(str(last) if last else "no model configured")


def _selftest() -> None:
    import os
    import tempfile
    from datetime import datetime

    from argon import clock

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["ARGON_HOME"] = tmp
        clock.set_for_test(datetime(2026, 9, 14, 18, 0, tzinfo=clock.TZ))

        assert _retryable(ProviderError("HTTP 503: busy"))
        assert _retryable(ProviderError("unreachable: timed out"))
        assert not _retryable(ProviderError("HTTP 400: bad request")), "400 will fail again"
        assert not _retryable(ProviderError("HTTP 401: bad key"))

        assert _usage({"usage": {"prompt_tokens": 18000, "completion_tokens": 150,
                                 "prompt_tokens_details": {"cached_tokens": 13000}}}
                      ).cached == 13000
        assert _usage({}).prompt == 0, "a reply with no usage block must not crash"

        # A failing endpoint raises and never yields deliverable text.
        cfg = Provider(api_key="x", api_base="http://127.0.0.1:1/v1", timeout_s=0.2)
        try:
            complete(cfg, [{"role": "user", "content": "hi"}], cap=5.0, attempts=1)
            raise AssertionError("should have raised")
        except ProviderError as e:
            assert "unreachable" in str(e)

        # Over the cap, nothing is attempted at all.
        budget.record("gpt-5.6-sol", budget.Usage(prompt=2_000_000))
        try:
            complete(cfg, [{"role": "user", "content": "hi"}], cap=5.0, attempts=1)
            raise AssertionError("should have refused")
        except budget.BudgetExceeded:
            pass

        clock.set_for_test(None)
        del os.environ["ARGON_HOME"]
    print("provider selftest ok")


if __name__ == "__main__":
    _selftest()
