#!/usr/bin/env bash
# install-daemon.sh — opt in to always-on 24/7 Overseer notifications.
# Installs two LaunchAgents so you get a native alert the moment a session needs
# you — even with the app and every terminal closed:
#   com.overseer.daemon  state transitions (needs-you / crashed / blocked)
#   com.overseer.guard   the moat: stalled (silent hang) / spiraling / over-budget
# Reversible: ./bin/install-daemon.sh --uninstall
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
LABELS=("com.overseer.daemon" "com.overseer.guard")

for LABEL in "${LABELS[@]}"; do
    DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
    if [ "${1:-}" = "--uninstall" ]; then
        launchctl unload "$DEST" 2>/dev/null || true
        rm -f "$DEST"
        echo "uninstalled $LABEL"
        continue
    fi
    mkdir -p "$HOME/Library/LaunchAgents"
    cp "$HERE/deploy/$LABEL.plist" "$DEST"
    launchctl unload "$DEST" 2>/dev/null || true   # idempotent reload
    launchctl load "$DEST"
    echo "loaded $LABEL"
done

if [ "${1:-}" = "--uninstall" ]; then
    echo "all Overseer notification agents stopped."
else
    echo "24/7 notifications on: state transitions + stalled/spiraling/over-budget."
    echo "  stop:  ./bin/install-daemon.sh --uninstall"
    echo "  logs:  tail -f '$HERE/data/'{daemon,guard}.log"
fi
