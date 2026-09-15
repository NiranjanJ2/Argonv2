"""Discord, and how a channel attaches to the runtime.

A channel is two halves: something that turns an inbound message into
``runtime.receive``, and a ``send`` callable registered with
``runtime.add_channel``.  Nothing else.  Channels hold no state and make no
decisions — the old system had delivery-target selection logic that silently
fell back to the CLI when a session file went missing, and two days of
check-ins went nowhere.

Discord runs its own asyncio loop in a thread.  ``receive`` is synchronous and
can take thirty seconds, so it goes to a worker rather than blocking the
gateway heartbeat.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from loguru import logger

from argon.config import Discord as DiscordConfig

#: Discord refuses anything longer.
MAX_CHARS = 2000


def chunk(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split on paragraph then line boundaries, never mid-word."""
    if len(text) <= limit:
        return [text]
    out, current = [], ""
    for para in text.split("\n"):
        candidate = f"{current}\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            out.append(current)
        while len(para) > limit:
            cut = para.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            out.append(para[:cut])
            para = para[cut:].lstrip()
        current = para
    if current:
        out.append(current)
    return out


class DiscordChannel:
    name = "discord"

    def __init__(self, cfg: DiscordConfig, on_message: Callable[[str], None]) -> None:
        self.cfg = cfg
        self.on_message = on_message
        self._loop: asyncio.AbstractEventLoop | None = None
        self._channel_id: int | None = None
        self._client = None
        self.ready = False
        self.error: str | None = None

    def start(self) -> None:
        import discord

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready():  # noqa: ANN001
            # Say so out loud. A channel that connects silently is
            # indistinguishable from one that failed silently, and "is Discord
            # actually up" should never need a packet capture to answer.
            self.ready = True
            logger.info("discord connected as {}", client.user)

        @client.event
        async def on_message(message):  # noqa: ANN001
            if message.author == client.user:
                return
            # Allow-list is the only authorisation. Empty means nobody, not
            # everybody — an open bot on a public server answers strangers.
            if str(message.author.id) not in self.cfg.allow_from:
                return
            self._channel_id = message.channel.id
            text = message.content.strip()
            if text:
                await asyncio.to_thread(self.on_message, text)

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(client.start(self.cfg.token))
            except Exception as e:  # noqa: BLE001
                # A bad token raises in this thread and would otherwise vanish,
                # leaving a gateway that looks healthy and answers nothing.
                self.error = f"{type(e).__name__}: {e}"
                logger.error("discord failed: {}", self.error)

        threading.Thread(target=run, daemon=True, name="discord").start()

    def send(self, text: str) -> None:
        """Called from the agent's thread, so hop to Discord's loop."""
        if not (self._loop and self._client and self._channel_id):
            return
        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            return

        async def deliver() -> None:
            for part in chunk(text):
                await channel.send(part)

        asyncio.run_coroutine_threadsafe(deliver(), self._loop)


def _selftest() -> None:
    assert chunk("short") == ["short"]

    long_text = "\n".join(f"paragraph {i} " + "x" * 100 for i in range(40))
    parts = chunk(long_text)
    assert len(parts) > 1
    assert all(len(p) <= MAX_CHARS for p in parts), [len(p) for p in parts]
    assert "".join(parts).replace("\n", "") == long_text.replace("\n", "")

    # A single unbroken paragraph still has to fit.
    parts = chunk("y" * 5000)
    assert all(len(p) <= MAX_CHARS for p in parts) and len(parts) == 3

    # Words are not cut when there is a sensible break.
    words = " ".join(["word"] * 900)
    assert all(not p.startswith("ord") for p in chunk(words))

    seen: list[str] = []
    ch = DiscordChannel(DiscordConfig(token="t", allow_from=["1"]), seen.append)
    ch.send("nothing is connected yet")  # must not raise
    assert seen == []
    assert ch.ready is False and ch.error is None, "starts neither up nor failed"
    print("channels selftest ok")


if __name__ == "__main__":
    _selftest()
