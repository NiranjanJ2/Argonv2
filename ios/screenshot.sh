#!/usr/bin/env bash
# Boot a simulator, install Argon, point it at the real server, and shoot every
# screen. Exists because the UI was designed blind once and it showed.
set -euo pipefail
cd "$(dirname "$0")/app"

DEVICE="${DEVICE:-iPhone 16e}"
OUT="${OUT:-/tmp/argon-shots}"
mkdir -p "$OUT"

runtime=$(xcrun simctl list runtimes | grep -oE 'com\.apple\.CoreSimulator\.SimRuntime\.iOS-[0-9-]+' | tail -1)
[ -n "$runtime" ] || { echo "no iOS runtime installed"; exit 1; }

udid=$(xcrun simctl list devices | grep -m1 "$DEVICE (" | grep -oE '[0-9A-F-]{36}' || true)
if [ -z "$udid" ]; then
  type=$(xcrun simctl list devicetypes | grep -m1 "$DEVICE" | grep -oE 'com\.apple\.CoreSimulator\.SimDeviceType\.[A-Za-z0-9-]+')
  udid=$(xcrun simctl create "argon-$DEVICE" "$type" "$runtime")
  echo "created $DEVICE ($udid)"
fi

xcrun simctl boot "$udid" 2>/dev/null || true
xcrun simctl bootstatus "$udid" -b >/dev/null 2>&1 || true

echo "building…"
xcodebuild -project foqos.xcodeproj -scheme foqos \
  -destination "id=$udid" -configuration Debug \
  CODE_SIGNING_ALLOWED=NO -derivedDataPath /tmp/argon-dd build >/tmp/argon-build.log 2>&1 \
  || { grep -E 'error:' /tmp/argon-build.log | head; exit 1; }

app=$(find /tmp/argon-dd/Build/Products -name 'foqos.app' -maxdepth 3 | head -1)
xcrun simctl install "$udid" "$app"

# Real server, real data — a screenshot of an empty offline state proves nothing.
BASE="${ARGON_BASE:-http://192.168.68.72:3997}"
TOKEN="${ARGON_TOKEN:-}"
if [ -n "$TOKEN" ]; then
  xcrun simctl spawn "$udid" defaults write com.niranjanj.argon argon.base -string "$BASE"
  xcrun simctl spawn "$udid" defaults write com.niranjanj.argon argon.token -string "$TOKEN"
fi

# Every screen, not just the first. There is no Simulator.app in this toolchain
# to tap a tab with, so the app reads its opening tab from a default and we
# relaunch once per screen.
for screen in today chat focus settings; do
  xcrun simctl terminate "$udid" com.niranjanj.argon >/dev/null 2>&1 || true
  xcrun simctl spawn "$udid" defaults write com.niranjanj.argon argon.tab -string "$screen"
  xcrun simctl launch "$udid" com.niranjanj.argon >/dev/null
  sleep 6
  xcrun simctl io "$udid" screenshot "$OUT/$screen.png" >/dev/null 2>&1
  echo "shot: $OUT/$screen.png"
done
echo "device $udid booted"
