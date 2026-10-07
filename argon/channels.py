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

    def __init__(self, cfg: DiscordConfig, on_message: Callable[[str, Callable[[str], None]], None],
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
            await self.handle(message)

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

    async def handle(self, message) -> None:  # noqa: ANN001
        if message.author == self._client.user:
            return
        # Allow-list is the only authorisation. Empty means nobody, not
        # everybody — an open bot on a public server answers strangers.
        if str(message.author.id) not in self.cfg.allow_from:
            return
        cid = message.channel.id
        # Only follow him into a private channel. Argon's briefs name his
        # assignments and his evening; one message from a public guild
        # channel would have redirected the next one there.
        private = getattr(message.guild, "id", None) is None
        if private and self._channel_id != cid:
            self._channel_id = cid
            if self.remember:
                self.remember(str(cid))
        text = message.content.strip()
        if text:
            # The reply is pinned to this message's channel, never re-read
            # from mutable state, so a later message can't redirect it.
            await asyncio.to_thread(self.on_message, text,
                                    lambda reply: self.send(reply, channel_id=cid))

    #: How long the agent thread waits for Discord to accept a message. Long
    #: enough for a REST round trip, short enough not to stall a turn.
    SEND_TIMEOUT_S = 20.0

    def send(self, text: str, channel_id: int | None = None) -> None:
        """Called from the agent's thread, so hop to Discord's loop and wait.

        Waiting matters: the caller has to learn whether this actually landed.
        Returning before the send resolves is how nine messages were recorded
        as sent while every one of them failed.
        """
        if not (self._loop and self._client):
            raise RuntimeError("discord is not connected")
        channel_id = channel_id or self._channel_id
        if not channel_id:
            raise RuntimeError("no discord channel known yet; set discord.channel_id")

        client = self._client

        async def deliver() -> None:
            # get_channel only knows guild channels and cached private ones, so
            # it returns None for a DM — which is what Argon actually uses.
            # fetch_channel asks the API and works for both.
            channel = client.get_channel(channel_id)
            if channel is None:
                channel = await client.fetch_channel(channel_id)
            for part in chunk(text):
                await channel.send(part)

        future = asyncio.run_coroutine_threadsafe(deliver(), self._loop)
        future.result(timeout=self.SEND_TIMEOUT_S)   # raises what the send raised


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
    ch = DiscordChannel(DiscordConfig(token="t", allow_from=["1"]),
                        lambda text, reply: seen.append(text))
    # Nowhere to send is an error, never a silent drop.
    try:
        ch.send("nothing is connected yet")
        raise AssertionError("should have raised")
    except RuntimeError as e:
        assert "not connected" in str(e)
    assert seen == []
    assert ch.ready is False and ch.error is None, "starts neither up nor failed"

    # A configured channel id is used before he has ever spoken.
    noop = lambda text, reply: None  # noqa: E731
    seeded = DiscordChannel(DiscordConfig(token="t", channel_id="1477811435487891629"), noop)
    assert seeded._channel_id == 1477811435487891629
    assert DiscordChannel(DiscordConfig(token="t", channel_id="nonsense"), noop)._channel_id is None

    # Inbound: replies go to the incoming channel; only DMs become the proactive one.
    from types import SimpleNamespace as NS

    def msg(author, channel, guild=None, content="hi"):
        return NS(author=NS(id=author), channel=NS(id=channel),
                  guild=NS(id=guild) if guild else None, content=content)

    sent, saved = [], []

    def on_message(text, reply):
        reply("pong")

    dm = DiscordChannel(DiscordConfig(token="t", allow_from=["1"], channel_id="500"),
                        on_message, remember=saved.append)
    dm._client = NS(user="bot")
    dm.send = lambda text, channel_id=None: sent.append((text, channel_id))
    asyncio.run(dm.handle(msg(1, 700)))                    # allowed DM
    assert sent == [("pong", 700)] and saved == ["700"] and dm._channel_id == 700, (sent, saved)
    asyncio.run(dm.handle(msg(1, 900, guild=42)))          # allowed guild message
    assert sent[-1] == ("pong", 900), sent
    assert saved == ["700"] and dm._channel_id == 700, "guild must not become the DM target"
    asyncio.run(dm.handle(msg(2, 701)))                    # unauthorised author
    asyncio.run(dm.handle(msg("bot", 702)))                # self; "bot" not allowed anyway
    dm._client.user = NS(id=1)
    own = msg(1, 703)
    own.author = dm._client.user
    asyncio.run(dm.handle(own))                           # genuine self-message
    assert len(sent) == 2 and saved == ["700"], (sent, saved)
    # Exercise send's real loop hop and explicit destination without Discord.
    loop = asyncio.new_event_loop()
    worker = threading.Thread(target=loop.run_forever)
    worker.start()
    landed, fetched = [], []
    class FakeChannel:
        async def send(self, text):
            landed.append(text)
    class FakeClient:
        def get_channel(self, channel_id):
            assert channel_id == 900, "use the pinned reply channel, not the saved DM"
            return None
        async def fetch_channel(self, channel_id):
            fetched.append(channel_id)
            return FakeChannel()
    real_send = DiscordChannel(DiscordConfig(channel_id="500"), noop)
    real_send._loop, real_send._client = loop, FakeClient()
    try:
        real_send.send("reply", channel_id=900)
        assert fetched == [900] and landed == ["reply"]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        worker.join(timeout=2)
        assert not worker.is_alive()
        loop.close()

    print("channels selftest ok")


if __name__ == "__main__":
    _selftest()
