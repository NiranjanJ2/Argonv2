#!/usr/bin/env bash
# Put both readouts where their hosts look for them.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$HOME/.argon"
cp "$here/argon-widget.py" "$HOME/.argon/argon-widget.py"
chmod +x "$HOME/.argon/argon-widget.py"

# Ask SwiftBar where it actually looks. The obvious guess — .../SwiftBar/Plugins
# — was wrong on this machine: SwiftBar was configured with .../SwiftBar itself,
# so a plugin installed into Plugins/ sat one level below where it scans and
# never ran. Its own preference is the only reliable answer.
swiftbar="${SWIFTBAR_PLUGIN_DIR:-$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null)}"
swiftbar="${swiftbar:-$HOME/Library/Application Support/SwiftBar/Plugins}"
if [ -d "$swiftbar" ]; then
  ln -sf "$HOME/.argon/argon-widget.py" "$swiftbar/argon.1m.py"
  echo "swiftbar  -> $swiftbar/argon.1m.py"
else
  echo "swiftbar  -- not installed, skipped"
fi

ubersicht="$HOME/Library/Application Support/Übersicht/widgets"
if [ -d "$ubersicht" ]; then
  cp "$here/argon.jsx" "$ubersicht/argon.jsx"
  echo "ubersicht -> $ubersicht/argon.jsx"
else
  echo "ubersicht -- not installed, skipped"
fi

if [ ! -f "$HOME/.argon-widget.json" ]; then
  cat > "$HOME/.argon-widget.json" <<JSON
{"base": "https://argon.agentneon.dev", "token": "PUT-THE-API-TOKEN-HERE"}
JSON
  echo "config    -> ~/.argon-widget.json (add the token)"
fi
