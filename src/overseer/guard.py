"""guard — the always-on guardrail watcher: the moat made ACTIONABLE.

Polls the unified attention feed (alerts.attention: stalled / spiraling /
over-budget across ALL sessions) plus the owned-children supervisor
(needs-human / crashed / stuck) and fires a native notification the moment a
session TRULY needs the operator — debounced per (session, kind) so it never nags or
spams. This is the payoff of the whole stack: "ping me only when it matters,"
even when the cockpit app and every terminal are closed.

Run: `overseer guard [interval]`  (or via launchd — deploy/com.overseer.guard.plist).
Respects OVERSEER_NOTIFY (silent otherwise) like the rest of notify.
"""
from __future__ import annotations

import time

from . import notify

DEBOUNCE_SEC = 180.0


def collect() -> list[tuple[str, str, str]]:
    """Return (dedupe_key, title, body) for every session that needs the operator right now."""
    out: list[tuple[str, str, str]] = []
    try:
        from . import alerts
        for a in alerts.attention().get("alerts", []):
            out.append((f"{a['session_id']}:{a['kind']}",
                        f"{a['name']} — {a['kind']}", a.get("why", "")))
    except Exception:
        pass
    try:
        from . import supervisor
        for c in supervisor.summary().get("needs_attention", []):
            out.append((f"{c['name']}:{c['state']}",
                        f"{c['name']} — {c['state']}", c.get("hint", "")))
    except Exception:
        pass
    return out


def tick(last: dict, now: float | None = None, fire=None) -> list[str]:
    """One guard cycle: fire (debounced) for anything needing attention. `fire` is
    the notification callback (defaults to notify._osascript); returns keys fired."""
    now = now if now is not None else time.time()
    fire = fire or (lambda title, body: notify._osascript(title, body))
    fired = []
    for key, title, body in collect():
        if now - last.get(key, 0) >= DEBOUNCE_SEC:
            fire("Overseer", f"{title}: {body}"[:180] if body else title)
            last[key] = now
            fired.append(key)
    return fired


def run(interval: float = 10.0) -> int:
    import sys
    if not notify.enabled():
        print("guard: set OVERSEER_NOTIFY=1 to enable notifications", file=sys.stderr)
    print(f"overseer guard: watching every {interval}s (Ctrl-C to stop)", file=sys.stderr)
    last: dict = {}
    while True:
        try:
            fired = tick(last)
            if fired:
                print(f"[{time.strftime('%H:%M:%S')}] notified: {', '.join(fired)}", file=sys.stderr)
        except Exception as e:  # never let the watcher die
            print(f"[{time.strftime('%H:%M:%S')}] guard error: {e}", file=sys.stderr)
        time.sleep(interval)
