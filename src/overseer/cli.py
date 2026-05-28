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

from . import notify, roster
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


def cmd_demo(argv: list[str]) -> int:
    """`overseer demo [port]` — serve a board seeded with every state (synthetic).

    Tom's real fleet is usually all-healthy, so this shows what the alert states
    (needs-you / crashed / blocked) actually look like. Synthetic data only.
    """
    import os
    from pathlib import Path
    from . import web
    demo_db = str(Path.home() / "overseer" / "data" / "demo.db")
    try:
        os.remove(demo_db)
    except OSError:
        pass
    s = Store(demo_db)
    now = time.time()
    synthetic = [
        ("helix-ios", "needs-human", "assistant:tool_use", 40, "needs your approval on a tool"),
        ("directstack-x", "crashed", "assistant:tool_use", 95, "died mid-tool (400 thinking-block)"),
        ("vault-sync", "blocked", "assistant:thinking", 720, "busy 12m, transcript frozen"),
        ("davinci-edit", "working", "assistant:tool_use", 3, "running a tool"),
        ("growth-graph", "working", "user:tool_result", 12, "processing"),
        ("flote-api", "waiting", "assistant:text", 480, "turn done, waiting for you"),
        ("note-autopost", "done", "assistant:text", 1500, "finished cleanly"),
    ]
    for i, (name, st, ev, age, _note) in enumerate(synthetic):
        s.upsert_lead({
            "session_id": f"demo{i:02d}-0000-0000-0000-{i:012d}", "pid": 10000 + i,
            "name": name, "cwd": "/Users/example", "domain": f"-Users-example-{name}",
            "raw_status": "busy" if st in ("working", "blocked") else "idle",
            "derived_state": st, "last_event": ev,
            "last_mtime": now - age, "started_at": now - age - 1000,
        })
    s.close()
    port = int(argv[0]) if argv else 8899
    print("overseer demo: synthetic board (every state). Real data is NOT shown here.")
    return web.serve(port=port, poll=False, db_path=demo_db)


def cmd_doctor(store: Store) -> int:
    """`overseer doctor` — environment self-check."""
    import shutil
    from pathlib import Path
    from . import lease

    def line(ok, label, detail=""):
        print(f"  [{'OK ' if ok else 'XX '}] {label}{('  -  ' + detail) if detail else ''}")

    print("\n  OVERSEER doctor\n")
    claude = shutil.which("claude")
    line(bool(claude), "claude CLI on PATH", claude or "NOT FOUND")
    try:
        n = len(roster.fetch())
        line(True, "claude agents --json", f"{n} live sessions")
    except Exception as e:
        line(False, "claude agents --json", str(e)[:60])
    proj = Path.home() / ".claude" / "projects"
    line(proj.exists(), "transcript dir", str(proj))
    try:
        store.log_event("__doctor__", "ping", "")
        line(True, "sqlite writable", str(store.db_path))
    except Exception as e:
        line(False, "sqlite writable", str(e)[:60])
    leases = list(lease.LEASE_DIR.glob("*.json")) if lease.LEASE_DIR.exists() else []
    line(True, "lease hook (v2)", f"{len(leases)} fresh-ish leases — "
         + ("installed" if leases else "NOT installed (see README 'opt-in lease hook')"))
    print()
    return 0


def cmd_restart(store: Store, args: list[str]) -> int:
    """`overseer restart <session_id_prefix>` — DRY-RUN restart planner (no execution)."""
    from . import recover
    try:
        scan_once(store)
    except roster.RosterError as e:
        print(f"overseer: roster error: {e} (using last-known state)", file=sys.stderr)
    rows = {r["session_id"]: dict(r) for r in store.all_leads()}
    if not args:
        # show all restartable leads
        plans = [recover.plan_restart(r) for r in rows.values()]
        restartable = [p for p in plans if p.restartable]
        print(f"\n  restartable leads: {len(restartable)} (dry-run; nothing executed)\n")
        for p in restartable:
            print("  " + p.as_text().replace("\n", "\n  "))
        if not restartable:
            print("  (no crashed/done/gone leads right now)")
        print("\n  to plan a specific one: overseer restart <session_id_prefix>\n")
        return 0
    prefix = args[0]
    match = next((sid for sid in rows if sid.startswith(prefix)), None)
    if not match:
        print(f"no lead matching '{prefix}'", file=sys.stderr)
        return 1
    plan = recover.plan_restart(rows[match])
    print("\n" + plan.as_text())
    print("\n  (DRY-RUN — execution is gated until lease/quiescence guards exist; "
          "see docs/STATE-TRUTH.md)\n")
    return 0


def cmd_daemon(store: Store, interval: float = 5.0) -> int:
    print(f"overseer daemon: polling every {interval}s (Ctrl-C to stop)", file=sys.stderr)
    while True:
        try:
            counts = scan_once(store, on_transition=notify.on_transition)
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
        if cmd == "serve":
            from . import web
            store.close()  # serve manages its own connections (threaded)
            port = int(argv[1]) if len(argv) > 1 else 8899
            return web.serve(port=port)
        if cmd == "restart":
            return cmd_restart(store, argv[1:])
        if cmd == "doctor":
            return cmd_doctor(store)
        if cmd == "demo":
            store.close()
            return cmd_demo(argv[1:])
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
