#!/usr/bin/env bash
# Archive Argon for TestFlight.
#
# Archiving signs with the Apple *Development* identity — that is what build 22
# used and what shipped. Xcode re-signs for distribution at export time, which
# is why a distribution certificate is not needed until you press Distribute.
#
#   ./release.sh            bump the build number and archive
#   ./release.sh --no-bump  archive at the current build number
#
# Then: Xcode → Window → Organizer → Archives → Distribute App → TestFlight.
set -euo pipefail
cd "$(dirname "$0")/app"

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
echo "next: Xcode → Window → Organizer → Archives → Distribute App → TestFlight"
