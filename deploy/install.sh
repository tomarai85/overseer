#!/bin/bash
# Install the Overseer LaunchAgents for the CURRENT user and repo location.
#
# The plists are templates because launchd does not expand $HOME inside a plist file — the path
# has to be literal, so it has to be written at install time rather than committed.
set -euo pipefail
HOME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS"

for t in "$HOME_DIR"/deploy/*.plist.template; do
    out="$AGENTS/$(basename "${t%.template}")"
    sed "s|__OVERSEER_HOME__|$HOME_DIR|g" "$t" > "$out"
    label="$(basename "${out%.plist}")"
    launchctl bootout "gui/$UID/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$out"
    echo "installed $label -> $out"
done
echo "done. uninstall: launchctl bootout gui/$UID/<label> && rm $AGENTS/com.overseer.*.plist"
