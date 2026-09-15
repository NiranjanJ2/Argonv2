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
  if out=$(xcrun swiftc -typecheck -sdk "$sdk" -target arm64-apple-ios17.0 \
           -swift-version 5 ios/Argon/*.swift 2>&1); then
    printf '  ok    ios (typecheck)\n'
  else
    printf '  FAIL  ios\n%s\n' "$out"; fail=1
  fi
else
  printf '  skip  ios (no iphoneos SDK)\n'
fi

exit $fail
