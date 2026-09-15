# Deploying

Two units, because the box has two ways in.

- `argon.service` — system unit, needs root. `sudo cp deploy/argon.service
  /etc/systemd/system/argonv2.service && sudo systemctl enable --now argonv2`
- `argonv2.user.service` — user unit, no root. What is running now:
  `systemctl --user enable --now argonv2` plus `loginctl enable-linger` so it
  survives logout.

v2 runs on **3997**; v1 stays on 3995 until you retire it. Both can run at once
— they share nothing. v2's state is `~/.argon2`, v1's is `~/.argon`.

## Firewall

`ufw` only knows about 3995, so 3997 answers on localhost but not from the LAN.
One command opens it:

```sh
sudo ufw allow 3997/tcp comment 'argon v2'
```

Until then, reach it with `ssh -L 3997:localhost:3997 agentneon`.

## Discord

v2 ships with Discord **disabled**. v1 holds the same bot token and both would
answer every message. Flip `discord.enabled` in `~/.argon2/config.json` and
stop v1 in the same breath.
