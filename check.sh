#!/usr/bin/env bash
# Every self-check in the project. No pytest, no fixtures — each module proves
# itself with asserts and `python -m argon.<module>` runs it.
set -uo pipefail
cd "$(dirname "$0")"
python="${PYTHON:-.venv/bin/python}"
[ -x "$python" ] || python="python3"

modules=(clock transcript context schedule config budget provider store tools
         agent bell channels runtime api integrations.google integrations.push
         integrations.ac)

fail=0
for m in "${modules[@]}"; do
  if out=$("$python" -m "argon.$m" 2>&1); then
    printf '  ok    %s\n' "$m"
  else
    printf '  FAIL  %s\n%s\n' "$m" "$out"
    fail=1
  fi
done

if out=$(python3 desktop/argon-widget.py --selftest 2>&1); then
  printf '  ok    desktop/argon-widget\n'
else
  printf '  FAIL  desktop/argon-widget\n%s\n' "$out"; fail=1
fi

if command -v xcrun >/dev/null 2>&1 && sdk=$(xcrun --sdk iphoneos --show-sdk-path 2>/dev/null); then
  # No standalone typecheck of the Argon layer any more: several of those
  # files legitimately reference Foqos types (SharedData, TimerActivity,
  # AppBlockerUtil), so they only typecheck in the app. The full app build
  # below covers them properly.
  # The sync layer is pure Foundation, so it actually runs on the Mac.
  if out=$(cd ios && swift test 2>&1); then
    n=$(printf '%s' "$out" | grep -o "Executed [0-9]* tests" | tail -1)
    printf '  ok    ios sync tests (%s)\n' "${n:-ran}"
  else
    printf '  FAIL  ios sync tests\n%s\n' "$(printf '%s' "$out" | tail -20)"; fail=1
  fi
else
  printf '  skip  ios (no iphoneos SDK)\n'
fi

# The whole app, every target. Slow (~2 min), so opt out with SKIP_APP=1.
if [ "${SKIP_APP:-0}" != "1" ] && [ -d ios/app/foqos.xcodeproj ] && command -v xcodebuild >/dev/null 2>&1; then
  if out=$(cd ios/app && xcodebuild -project foqos.xcodeproj -scheme foqos \
           -destination 'generic/platform=iOS Simulator' -configuration Debug \
           CODE_SIGNING_ALLOWED=NO build 2>&1); then
    printf '  ok    ios app (all targets build)\n'
  else
    printf '  FAIL  ios app\n%s\n' "$(printf '%s' "$out" | grep -E 'error:' | head -10)"
    fail=1
  fi
else
  printf '  skip  ios app (SKIP_APP or no xcodebuild)\n'
fi

exit $fail
