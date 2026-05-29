#!/usr/bin/env bash
# build-app.sh — package the Swift cockpit as a double-click Overseer.app bundle.
#   ./bin/build-app.sh        -> dist/Overseer.app (open it / drag to /Applications)
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HERE/dist/Overseer.app"
CONTENTS="$APP/Contents"

echo "building release binary..."
swift build -c release --package-path "$HERE/app"

echo "assembling $APP ..."
rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"
cp "$HERE/app/.build/release/OverseerApp" "$CONTENTS/MacOS/Overseer"
cp "$HERE/app/Info.plist" "$CONTENTS/Info.plist"
# app icon: regenerate from source if missing, then bundle it
[ -f "$HERE/app/Resources/Overseer.icns" ] || swift "$HERE/app/tools/make-icon.swift" >/dev/null 2>&1 || true
[ -f "$HERE/app/Resources/Overseer.icns" ] && cp "$HERE/app/Resources/Overseer.icns" "$CONTENTS/Resources/" || true

# ad-hoc sign so Gatekeeper lets a locally-built app run (real notarization is
# Tom's distribution step; ad-hoc is fine for personal/dogfood use)
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "(codesign skipped)"

echo "done: $APP"
echo "  open it:    open '$APP'"
echo "  install:    cp -r '$APP' /Applications/"
