"""The model call.  OpenAI Responses API, stdlib only.

**Why Responses and not Chat Completions.**  Luna (5.6 and 6) refuses function
tools together with ``reasoning_effort`` on ``/chat/completions`` — it is
reasoning or tools, pick one — and every Argon turn carries tools.  From
2026-09-21 every call 400'd on that and fell through to ``gpt-5-mini`` without
a word.  ``/responses`` allows both, and carries the model's reasoning from one
tool step to the next (``Reply.items``), which Chat Completions cannot.

The rest of Argon still speaks chat-shaped messages and tool schemas; they are
translated here, at the boundary, so ``context.py``'s cache-shaped prefix and
``agent.py``'s loop did not have to change.

ponytail: Responses only.  Every other OpenAI-compatible host (NIM, Groq) only
has ``/chat/completions``; add the old path back behind a config switch if a
non-OpenAI provider is ever used again.

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
    #: The raw output items — reasoning included — to send back verbatim on the
    #: next step of the same turn. Rebuilding them from ``tool_calls`` would drop
    #: the reasoning, and the model would re-derive its plan every tool step.
    items: list[dict[str, Any]] = field(default_factory=list)
    #: Why the primary model was skipped, when the fallback answered. Silent
    #: fallback is how a week of gpt-5-mini went unnoticed.
    fell_back: str = ""


def _post(url: str, key: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        raise ProviderError(f"HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ProviderError(f"unreachable: {e}") from e

    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        # A captive portal, a proxy error page or a truncated body arrives as
        # 200 with HTML. Decoding outside the guard let that escape the whole
        # turn as an unhandled exception.
        raise ProviderError(f"bad response body: {body[:200]!r}") from e


def _retryable(exc: ProviderError) -> bool:
    text = str(exc)
    if text.startswith("unreachable"):
        return True
    code = text[5:8] if text.startswith("HTTP ") else ""
    return code.isdigit() and int(code) in RETRYABLE


def _usage(raw: dict) -> budget.Usage:
    u = raw.get("usage") or {}
    details = u.get("input_tokens_details") or {}
    return budget.Usage(
        prompt=int(u.get("input_tokens") or 0),
        completion=int(u.get("output_tokens") or 0),
        cached=int(details.get("cached_tokens") or 0),
        written=int(details.get("cache_write_tokens") or 0),
    )


def _tool(schema: dict) -> dict:
    """A chat tool schema, flattened the way /responses wants it."""
    fn = schema.get("function") or schema
    return {"type": "function", "name": fn["name"],
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}}}


def _input(messages: list[dict]) -> list[dict]:
    """Chat messages as Responses input items, in the same order.

    Order and content are preserved exactly, so a prefix that was byte-stable
    as chat messages is still byte-stable here and the cache keeps hitting.
    """
    items: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            items.append({"type": "function_call_output",
                          "call_id": m["tool_call_id"], "output": m.get("content") or ""})
        elif role == "assistant" and m.get("_items"):
            items.extend(m["_items"])  # this turn's own output, reasoning and all
        elif role == "assistant":
            if m.get("content"):
                items.append({"role": "assistant", "content": m["content"]})
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                items.append({"type": "function_call", "call_id": call.get("id", ""),
                              "name": fn.get("name", ""), "arguments": fn.get("arguments", "{}")})
        else:
            items.append({"role": role, "content": m.get("content") or ""})
    return items


def _output(raw: dict) -> tuple[str, list[dict], list[dict]]:
    """(text, chat-shaped tool calls, raw items to replay)."""
    text, calls, replay = [], [], []
    for item in raw.get("output") or []:
        kind = item.get("type")
        if kind == "message":
            text += [c.get("text", "") for c in item.get("content") or []
                     if c.get("type") == "output_text"]
        elif kind == "function_call":
            calls.append({"id": item.get("call_id", ""), "type": "function",
                          "function": {"name": item.get("name", ""),
                                       "arguments": item.get("arguments") or "{}"}})
        if kind in ("reasoning", "function_call", "message"):
            # The server-side id is meaningless with store=false and rejected
            # when echoed back for some item types; the content is what counts.
            replay.append({k: v for k, v in item.items() if k not in ("id", "status")})
    return "".join(text), calls, replay


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
    url = cfg.api_base.rstrip("/") + "/responses"
    last: ProviderError | None = None
    skipped = ""

    for model in (cfg.model, cfg.fallback_model):
        if not model:
            continue
        budget.check(cap, model)  # refuses before spending, not after
        body: dict[str, Any] = {
            "model": model,
            "input": _input(messages),
            # Nothing is kept on OpenAI's side; the transcript is the state.
            # Reasoning comes back encrypted so the next tool step can resend it.
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if cfg.reasoning_effort:
            body["reasoning"] = {"effort": cfg.reasoning_effort}
        if tools:
            body["tools"] = [_tool(t) for t in tools]
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

            if raw.get("error"):
                last = ProviderError(f"error in body: {str(raw['error'])[:300]}")
                break
            text, calls, items = _output(raw)
            usage = _usage(raw)
            return Reply(
                text=text,
                tool_calls=calls,
                model=model,
                usage=usage,
                cost=budget.record(model, usage),
                items=items,
                fell_back=skipped,
            )
        skipped = f"{model}: {last}"

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
        assert not _retryable(ProviderError("bad response body: b'<html>'"))

        u = _usage({"usage": {"input_tokens": 18000, "output_tokens": 150,
                              "input_tokens_details": {"cached_tokens": 13000,
                                                       "cache_write_tokens": 5000}}})
        assert (u.prompt, u.cached, u.written) == (18000, 13000, 5000)
        assert _usage({}).prompt == 0, "a reply with no usage block must not crash"

        # Translation both ways. A tool round trip has to arrive as the items
        # /responses expects, in the same order the prefix was cached in.
        chat = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "list_tasks", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "HW 23"}]
        assert [i.get("type") or i["role"] for i in _input(chat)] == \
            ["system", "user", "function_call", "function_call_output"]
        replayed = [{"role": "assistant", "content": None, "tool_calls": [],
                     "_items": [{"type": "reasoning", "encrypted_content": "x"}]}]
        assert _input(replayed) == [{"type": "reasoning", "encrypted_content": "x"}], \
            "this turn's reasoning goes back verbatim"
        assert _tool({"type": "function", "function": {"name": "say", "description": "d",
                      "parameters": {"type": "object"}}})["name"] == "say"
        text, calls, items = _output({"output": [
            {"type": "reasoning", "id": "rs_1", "encrypted_content": "e"},
            {"type": "function_call", "id": "fc_1", "call_id": "c9", "name": "say",
             "arguments": '{"text":"ok"}', "status": "completed"},
            {"type": "message", "content": [{"type": "output_text", "text": "done"}]}]})
        assert text == "done" and calls[0]["id"] == "c9"
        assert calls[0]["function"]["name"] == "say"
        assert all("id" not in i for i in items), "server ids are not replayed"

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
