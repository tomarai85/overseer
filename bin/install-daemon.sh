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
# Add the web board too with: ./bin/install-daemon.sh --with-web
[ "${1:-}" = "--with-web" ] && LABELS+=("com.overseer.web")

# The plists are shipped as *.plist.template with a __OVERSEER_HOME__ placeholder, because launchd
# does not expand $HOME inside a plist — the path must be literal, so it cannot be committed. This
# used to `cp deploy/$LABEL.plist`, which stopped existing when the templates were introduced.
for LABEL in "${LABELS[@]}"; do
    DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
    if [ "${1:-}" = "--uninstall" ]; then
        launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
        rm -f "$DEST"
        echo "uninstalled $LABEL"
        continue
    fi
    TMPL="$HERE/deploy/$LABEL.plist.template"
    [ -f "$TMPL" ] || { echo "missing template: $TMPL" >&2; exit 1; }
    mkdir -p "$HOME/Library/LaunchAgents"
    sed "s|__OVERSEER_HOME__|$HERE|g" "$TMPL" > "$DEST"
    grep -q '__OVERSEER_HOME__' "$DEST" && { echo "substitution failed for $LABEL" >&2; exit 1; }
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true   # idempotent reload
    launchctl bootstrap "gui/$UID" "$DEST"
    echo "loaded $LABEL"
done

if [ "${1:-}" = "--uninstall" ]; then
    echo "all Overseer notification agents stopped."
else
    echo "24/7 notifications on: state transitions + stalled/spiraling/over-budget."
    echo "  stop:  ./bin/install-daemon.sh --uninstall"
    echo "  logs:  tail -f '$HERE/data/'{daemon,guard}.log"
fi
