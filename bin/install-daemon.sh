#!/usr/bin/env bash
# install-daemon.sh — opt in to always-on 24/7 Overseer notifications.
# Copies the notify-daemon LaunchAgent into place and loads it, so you get a
# native alert the moment any session needs you / crashes — even with the app
# (and your terminals) closed. Reversible: ./bin/install-daemon.sh --uninstall
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.overseer.daemon"
PLIST="$HERE/deploy/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "${1:-}" = "--uninstall" ]; then
    launchctl unload "$DEST" 2>/dev/null || true
    rm -f "$DEST"
    echo "uninstalled $LABEL (notifications stopped)"
    exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST" "$DEST"
launchctl unload "$DEST" 2>/dev/null || true   # idempotent reload
launchctl load "$DEST"
echo "loaded $LABEL — 24/7 needs-you / crashed / blocked notifications are now on."
echo "  stop:  ./bin/install-daemon.sh --uninstall"
echo "  log:   tail -f '$HERE/data/daemon.log'"
