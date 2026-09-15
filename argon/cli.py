"""Command line.  Argument parsing only — behaviour lives in runtime.py."""

from __future__ import annotations

import argparse
import sys
import threading
import time

from argon import budget, clock, config, schedule


def cmd_gateway(args) -> int:
    """Run everything: tick loop, HTTP surface, Discord."""
    from argon.api import create_app
    from argon.channels import DiscordChannel
    from argon.runtime import Runtime

    rt = Runtime()
    if rt.cfg.apns.enabled:
        rt.add_channel(rt.push_channel)
    discord = None
    if rt.cfg.discord.enabled and rt.cfg.discord.token:
        discord = DiscordChannel(rt.cfg.discord,
                                 lambda text: rt.receive(text, source="discord"),
                                 remember=rt.remember_discord_channel)
        discord.start()
        rt.add_channel(discord.send)
        # Wait briefly for the login to resolve so the banner tells the truth
        # rather than reporting "enabled" for a token the gateway rejected.
        for _ in range(20):
            if discord.ready or discord.error:
                break
            time.sleep(0.5)
        print(f"discord {'connected' if discord.ready else 'FAILED: ' + (discord.error or 'timeout')}")
    else:
        print("discord disabled")

    if rt.cfg.api.token:
        # ponytail: Flask's own server, threaded. One user on a LAN behind a
        # bearer token — put waitress in front of it when something other than
        # his phone and his laptop starts calling this.
        app = create_app(rt)
        threading.Thread(
            target=lambda: app.run(host=rt.cfg.api.host, port=rt.cfg.api.port,
                                   threaded=True, use_reloader=False),
            daemon=True, name="api").start()
        print(f"api on {rt.cfg.api.host}:{rt.cfg.api.port}")
    else:
        print("api disabled: no token in config", file=sys.stderr)

    print(f"ticking every {schedule.TICK_MINUTES}m in window; "
          f"now={'yes' if schedule.should_tick() else 'no'}")
    try:
        rt.run()
    except KeyboardInterrupt:
        rt.stop()
    return 0


def cmd_chat(args) -> int:
    """Talk to Argon from the terminal."""
    from argon.runtime import Runtime

    rt = Runtime()
    rt.add_channel(lambda text: print(f"\nargon> {text}\n"))
    if args.message:
        rt.receive(" ".join(args.message), source="cli")
        return 0
    print("argon chat — ctrl-d to quit")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if line:
            out = rt.receive(line, source="cli")
            if out.error:
                print(f"[{out.error}]", file=sys.stderr)


def cmd_tick(args) -> int:
    """Run one tick now, ignoring the window.  For seeing what it would do."""
    from argon.runtime import Runtime

    rt = Runtime()
    rt.add_channel(lambda text: print(f"SAID: {text}"))
    rt.transcript.append("tick")
    out = rt.turn(background=True)
    print(f"spoke={out.spoke} steps={out.steps} cost=${out.cost:.5f} "
          f"tools={out.tools_used} error={out.error or 'none'}")
    if out.text and not out.spoke:
        print(f"(thinking, not delivered): {out.text[:400]}")
    return 0


def _section(label: str, work) -> None:
    """Run one diagnostic. A section that explodes must not take the rest with
    it — doctor is what you reach for when things are already broken."""
    try:
        result = work()
    except Exception as e:  # noqa: BLE001
        print(f"{label:15s}FAILED: {type(e).__name__}: {e}")
        return
    if result is None:
        return
    lines = str(result).splitlines()
    print(f"{label:15s}{lines[0] if lines else ''}")
    for line in lines[1:]:
        print(f"{'':15s}{line}")


def cmd_doctor(args) -> int:
    """Check the things that have actually broken before."""
    from argon.integrations.google import capabilities, status
    from argon.integrations.push import Push, PushError
    from argon.runtime import Runtime

    rt = Runtime()
    cfg = rt.cfg
    print(f"home           {config.home()}")
    print(f"now            {clock.now():%Y-%m-%d %H:%M %Z} (ticking: {schedule.should_tick()})")

    month = budget.month()
    print(f"spend          ${month['usd']:.4f} of ${cfg.monthly_cap_usd:.2f} "
          f"over {month['calls']} calls")
    frac = budget.cached_fraction()
    verdict = "ok" if frac > 0.5 or month["calls"] < 5 else "LOW — prefix is drifting"
    print(f"prompt cache   {frac:.0%} of prompt tokens cached ({verdict})")

    print(f"provider       {cfg.provider.model} at {cfg.provider.api_base} "
          f"(key: {'set' if cfg.provider.api_key else 'MISSING'})")

    _section("google", lambda: status(cfg.google_accounts))
    _section("routing", lambda: "\n".join(
        f"{name:10s} -> {account or 'NOBODY'}"
        for name, account in capabilities(cfg.google_accounts).items()))

    if cfg.apns.enabled:
        _section("apns", lambda: Push(cfg.apns).probe())
        _section("device", lambda: "registered" if rt.device_token() else "NONE")
    else:
        print("apns           disabled")

    _section("discord", lambda: f"{'on' if cfg.discord.enabled else 'off'}, "
                                f"{len(cfg.discord.allow_from)} allowed")
    _section("transcript", lambda: f"{len(rt.transcript.window(2))} events in two days")
    _section("tasks", lambda: f"{len(rt.store.tasks())} open")
    return 0


def cmd_google_auth(args) -> int:
    from argon.integrations.google import authorise

    print("needs `ssh -L 8765:localhost:8765 agentneon` from the Mac")
    print(authorise(args.account))
    return 0


def cmd_ac(args) -> int:
    from argon.integrations.ac import Gree

    g = Gree()
    units = g.scan()
    if not units:
        print("no units answered")
        return 1
    for u in units:
        bound = g.bind(u)
        print(f"{bound.mac}  {bound.host}  {bound.name or '(unnamed)'}  key={bound.key}")
    print("\nput these in ~/.argon2/config.json if you want them to persist")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="argon")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gateway", help="run the assistant").set_defaults(fn=cmd_gateway)
    sub.add_parser("doctor", help="check what has broken before").set_defaults(fn=cmd_doctor)
    sub.add_parser("tick", help="run one tick now").set_defaults(fn=cmd_tick)
    sub.add_parser("ac-scan", help="find and bind air conditioners").set_defaults(fn=cmd_ac)

    chat = sub.add_parser("chat", help="talk to Argon")
    chat.add_argument("message", nargs="*")
    chat.set_defaults(fn=cmd_chat)

    auth = sub.add_parser("google-auth", help="authorise a Google account")
    auth.add_argument("account")
    auth.set_defaults(fn=cmd_google_auth)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
