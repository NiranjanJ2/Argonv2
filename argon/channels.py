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

import logging

from argon.config import Discord as DiscordConfig

log = logging.getLogger("argon.discord")

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

    def __init__(self, cfg: DiscordConfig, on_message: Callable[[str], None],
                 remember: Callable[[str], None] | None = None) -> None:
        self.cfg = cfg
        self.on_message = on_message
        #: Persists the last channel he used, so delivery survives a restart.
        self.remember = remember
        self._loop: asyncio.AbstractEventLoop | None = None
        # Seeded from config so the very first message has somewhere to go.
        self._channel_id: int | None = int(cfg.channel_id) if cfg.channel_id.isdigit() else None
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
            log.info("discord connected as %s", client.user)

        @client.event
        async def on_message(message):  # noqa: ANN001
            if message.author == client.user:
                return
            # Allow-list is the only authorisation. Empty means nobody, not
            # everybody — an open bot on a public server answers strangers.
            if str(message.author.id) not in self.cfg.allow_from:
                return
            if self._channel_id != message.channel.id:
                self._channel_id = message.channel.id
                if self.remember:
                    self.remember(str(message.channel.id))
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
                log.error("discord failed: %s", self.error)

        threading.Thread(target=run, daemon=True, name="discord").start()

    def send(self, text: str) -> None:
        """Called from the agent's thread, so hop to Discord's loop.

        Raises rather than returning quietly when there is nowhere to send:
        the runtime records a delivery failure, and a message that went nowhere
        is never mistaken for one that arrived.
        """
        if not (self._loop and self._client):
            raise RuntimeError("discord is not connected")
        if not self._channel_id:
            raise RuntimeError("no discord channel known yet; set discord.channel_id")
        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            raise RuntimeError(f"discord channel {self._channel_id} not visible to the bot")

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
    # Nowhere to send is an error, never a silent drop.
    try:
        ch.send("nothing is connected yet")
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "not connected" in str(e)
    assert seen == []
    assert ch.ready is False and ch.error is None, "starts neither up nor failed"

    # A configured channel id is used before he has ever spoken.
    seeded = DiscordChannel(DiscordConfig(token="t", channel_id="1477811435487891629"),
                            seen.append)
    assert seeded._channel_id == 1477811435487891629
    assert DiscordChannel(DiscordConfig(token="t", channel_id="nonsense"),
                          seen.append)._channel_id is None
    print("channels selftest ok")


if __name__ == "__main__":
    _selftest()
