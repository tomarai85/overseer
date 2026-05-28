"""cli — `overseer status | scan | daemon`.

Usage:
  python3 -m overseer.cli status      # scan once + print the board
  python3 -m overseer.cli scan        # scan once, no pretty print (json)
  python3 -m overseer.cli daemon      # run the poll loop (foreground)
"""
from __future__ import annotations

import json
import sys
import time

from . import roster
from .engine import scan_once
from .store import Store


# state -> (label, sort priority: things that need Tom float to the top)
_STATE_GLYPH = {
    "needs-human": ("[!] needs-you", 0),
    "crashed": ("[x] crashed", 1),
    "blocked": ("[~] blocked?", 2),
    "waiting": ("[.] waiting", 3),
    "working": ("[>] working", 4),
    "done": ("[v] done", 5),
    "gone": ("[ ] gone", 6),
    "unknown": ("[?] unknown", 7),
}


def _age(ts: float | None) -> str:
    if not ts:
        return "-"
    d = max(0, time.time() - ts)
    if d < 60:
        return f"{int(d)}s"
    if d < 3600:
        return f"{int(d/60)}m"
    return f"{int(d/3600)}h"


def cmd_status(store: Store) -> int:
    try:
        scan_once(store)
    except roster.RosterError as e:
        print(f"overseer: roster error: {e}", file=sys.stderr)
        # still show last-known from DB
    rows = [r for r in store.all_leads() if r["derived_state"] != "gone"]
    rows.sort(key=lambda r: _STATE_GLYPH.get(r["derived_state"], ("", 9))[1])
    print(f"\n  OVERSEER  -  {len(rows)} live leads  -  {time.strftime('%H:%M:%S')}\n")
    print(f"  {'STATE':<14} {'NAME / DOMAIN':<34} {'ACTIVITY':<9} {'SESSION'}")
    print("  " + "-" * 78)
    for r in rows:
        glyph = _STATE_GLYPH.get(r["derived_state"], (r["derived_state"], 9))[0]
        label = r["name"] or (r["domain"] or "?")
        if len(label) > 33:
            label = label[:30] + "..."
        print(f"  {glyph:<14} {label:<34} {_age(r['last_mtime']):<9} {r['session_id'][:8]}")
    print()
    return 0


def cmd_scan(store: Store) -> int:
    counts = scan_once(store)
    print(json.dumps(counts))
    return 0


def cmd_daemon(store: Store, interval: float = 5.0) -> int:
    print(f"overseer daemon: polling every {interval}s (Ctrl-C to stop)", file=sys.stderr)
    while True:
        try:
            counts = scan_once(store)
            ts = time.strftime("%H:%M:%S")
            summary = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            print(f"[{ts}] {summary}", file=sys.stderr)
        except roster.RosterError as e:
            print(f"[{time.strftime('%H:%M:%S')}] roster error: {e}", file=sys.stderr)
        except Exception as e:  # never let the loop die silently
            print(f"[{time.strftime('%H:%M:%S')}] unexpected: {e}", file=sys.stderr)
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "status"
    store = Store()
    try:
        if cmd == "status":
            return cmd_status(store)
        if cmd == "scan":
            return cmd_scan(store)
        if cmd == "daemon":
            interval = float(argv[1]) if len(argv) > 1 else 5.0
            return cmd_daemon(store, interval)
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
