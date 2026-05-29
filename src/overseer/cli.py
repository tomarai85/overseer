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


def cmd_supervise(_args: list[str]) -> int:
    """`overseer supervise` — away-mode summary over owned children (JSON)."""
    from . import supervisor
    print(json.dumps(supervisor.summary()))
    return 0


def cmd_leads(store: Store) -> int:
    """`overseer leads` — scan once + emit live leads as JSON (for the GUI shell)."""
    try:
        scan_once(store)
    except roster.RosterError:
        pass  # emit last-known
    rows = [r for r in store.all_leads() if r["derived_state"] != "gone"]
    rows.sort(key=lambda r: _STATE_GLYPH.get(r["derived_state"], ("", 9))[1])
    out = [{
        "session_id": r["session_id"],
        "name": r["name"] or (r["domain"] or "?"),
        "state": r["derived_state"],
        "age": _age(r["last_mtime"]),
    } for r in rows]
    print(json.dumps(out))
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


def cmd_spawn(args: list[str]) -> int:
    """`overseer spawn <name> [cwd]` — launch a Claude child in tmux + wait for ready."""
    from . import driver
    if not args:
        print("usage: overseer spawn <name> [cwd]", file=sys.stderr)
        return 2
    name, cwd = args[0], (args[1] if len(args) > 1 else None)
    try:
        sn = driver.spawn(name, cwd=cwd)
    except driver.DriverError as e:
        print(f"spawn failed: {e}", file=sys.stderr)
        return 1
    print(f"spawned '{name}' (tmux: {sn}) — booting...")
    if driver.wait_ready(name, timeout=40):
        print(f"child '{name}' is ready. send it work: overseer send {name} \"<prompt>\"")
        return 0
    print(f"child '{name}' spawned but did not reach the prompt in 40s "
          f"(check: overseer read {name})", file=sys.stderr)
    return 1


def cmd_send(args: list[str]) -> int:
    """`overseer send <name> <text...>` — type a prompt into a child and submit."""
    from . import driver
    if len(args) < 2:
        print("usage: overseer send <name> <text...>", file=sys.stderr)
        return 2
    name, text = args[0], " ".join(args[1:])
    try:
        driver.send(name, text)
    except driver.DriverError as e:
        print(f"send failed: {e}", file=sys.stderr)
        return 1
    print(f"sent to '{name}': {text}")
    return 0


def cmd_read(args: list[str]) -> int:
    """`overseer read <name> [lines]` — print the child's current screen."""
    from . import driver
    if not args:
        print("usage: overseer read <name> [lines]", file=sys.stderr)
        return 2
    name = args[0]
    lines = int(args[1]) if len(args) > 1 else 60
    try:
        print(driver.read(name, lines=lines))
    except driver.DriverError as e:
        print(f"read failed: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_children(_args: list[str]) -> int:
    """`overseer children` — list driver-managed children as JSON (for the GUI/parent)."""
    from . import driver
    print(json.dumps(driver.list_children()))
    return 0


def cmd_kill(args: list[str]) -> int:
    """`overseer kill <name>` — terminate a child."""
    from . import driver
    if not args:
        print("usage: overseer kill <name>", file=sys.stderr)
        return 2
    driver.kill(args[0])
    print(f"killed '{args[0]}'")
    return 0


def cmd_parent(args: list[str]) -> int:
    """`overseer parent-send <msg> | parent-history | parent-reset` — drive the
    fleet parent headlessly (used by the GUI; also usable from the shell)."""
    from . import parent
    sub = args[0] if args else "history"
    if sub == "send":
        msg = " ".join(args[1:])
        if not msg:
            print("usage: overseer parent-send <message>", file=sys.stderr)
            return 2
        try:
            print(json.dumps(parent.send(msg)))
        except parent.ParentError as e:
            print(json.dumps({"error": str(e)}))
            return 1
        return 0
    if sub == "history":
        print(json.dumps(parent.history()))
        return 0
    if sub == "reset":
        parent.reset()
        print(json.dumps({"reset": True}))
        return 0
    print(f"unknown parent subcommand: {sub}", file=sys.stderr)
    return 2


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
        if cmd == "leads":
            return cmd_leads(store)
        if cmd == "supervise":
            store.close()
            return cmd_supervise(argv[1:])
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
        if cmd in ("spawn", "send", "read", "children", "kill"):
            store.close()  # driver commands don't touch the db
            return {
                "spawn": cmd_spawn, "send": cmd_send, "read": cmd_read,
                "children": cmd_children, "kill": cmd_kill,
            }[cmd](argv[1:])
        if cmd in ("parent-send", "parent-history", "parent-reset"):
            store.close()
            return cmd_parent([cmd.split("-", 1)[1]] + argv[1:])
        if cmd == "parent":
            store.close()
            return cmd_parent(argv[1:])
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
