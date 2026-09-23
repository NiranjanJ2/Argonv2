#!/usr/bin/env bash
# Archive Argon and upload it to TestFlight.
#
# Archiving signs with the Apple *Development* identity — that is what build 22
# used and what shipped. Distribution signing happens at export, in the cloud,
# through the App Store Connect API key, so no distribution certificate is
# needed on this Mac.
#
#   ./release.sh                 bump the build number, archive, upload
#   ./release.sh --no-bump       archive at the current build number, upload
#   ./release.sh --upload PATH   upload an archive that already exists
#
# Without the key file it stops after archiving; then Xcode → Window →
# Organizer → Archives → Distribute App → TestFlight does the same by hand.
set -euo pipefail
cd "$(dirname "$0")/app"

# Not secrets — the .p8 is, and it never leaves ~/.appstoreconnect. The key
# must have the Admin role: cloud distribution signing refuses anything less.
ASC_KEY_ID=LFJV5A3K4Y
ASC_ISSUER=d067d6f1-4488-4ea2-912a-cc44d1dce353
ASC_KEY="$HOME/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID}.p8"

upload() {
  local archive="$1"
  [ -f "$ASC_KEY" ] || { echo "no API key at $ASC_KEY — distribute from Organizer"; return 1; }
  local opts out
  opts=$(mktemp -t argon-export).plist
  out=$(mktemp -d -t argon-export)
  cat > "$opts" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>upload</string>
  <key>teamID</key><string>DX3U2FC8X5</string>
  <key>signingStyle</key><string>automatic</string>
  <key>manageAppVersionAndBuildNumber</key><false/>
</dict></plist>
PLIST
  echo "uploading $(basename "$archive")…"
  xcodebuild -exportArchive -archivePath "$archive" -exportOptionsPlist "$opts" \
    -exportPath "$out" -allowProvisioningUpdates \
    -authenticationKeyPath "$ASC_KEY" -authenticationKeyID "$ASC_KEY_ID" \
    -authenticationKeyIssuerID "$ASC_ISSUER" 2>&1 \
    | grep -E 'error:|Upload succeeded|EXPORT' || true
}

if [ "${1:-}" = "--upload" ]; then
  upload "${2:?archive path}"
  exit
fi

PROJ=foqos.xcodeproj/project.pbxproj

if [ "${1:-}" != "--no-bump" ]; then
  current=$(grep -m1 -oE 'CURRENT_PROJECT_VERSION = [0-9]+' "$PROJ" | grep -oE '[0-9]+')
  next=$((current + 1))
  # Every target shares the build number; TestFlight rejects a duplicate.
  sed -i '' "s/CURRENT_PROJECT_VERSION = ${current};/CURRENT_PROJECT_VERSION = ${next};/g" "$PROJ"
  echo "build ${current} -> ${next}"
fi

version=$(grep -m1 -oE 'MARKETING_VERSION = [0-9.]+' "$PROJ" | grep -oE '[0-9.]+')
build=$(grep -m1 -oE 'CURRENT_PROJECT_VERSION = [0-9]+' "$PROJ" | grep -oE '[0-9]+')
stamp=$(date '+%Y-%m-%d %H.%M')
path="$HOME/Library/Developer/Xcode/Archives/$(date +%Y-%m-%d)/Argon ${stamp}.xcarchive"

echo "archiving ${version} (${build})…"
xcodebuild -project foqos.xcodeproj -scheme foqos \
  -destination 'generic/platform=iOS' -configuration Release \
  -archivePath "$path" -allowProvisioningUpdates archive \
  2>&1 | grep -E 'error:|Signing Identity|\*\* ARCHIVE' || true

[ -d "$path" ] || { echo "archive failed"; exit 1; }

# Prove the icon and every extension actually made it in — a build that
# succeeds with the wrong icon has happened here before.
app=$(find "$path/Products/Applications" -maxdepth 1 -name '*.app' | head -1)
echo
echo "archive : $path"
echo "version : $(plutil -extract ApplicationProperties.CFBundleShortVersionString raw "$path/Info.plist") ($(plutil -extract ApplicationProperties.CFBundleVersion raw "$path/Info.plist"))"
echo "extensions:"; ls "$app/PlugIns" | sed 's/^/  /'
icons=$(xcrun --sdk iphoneos assetutil --info "$app/Assets.car" 2>/dev/null \
        | python3 -c "import json,sys;d=json.load(sys.stdin);print(', '.join(sorted({e.get('Name','') for e in d if isinstance(e,dict) and 'argon-icon' in str(e.get('Name',''))})))" 2>/dev/null)
echo "icon    : ${icons:-NOT FOUND}"
echo
upload "$path"
