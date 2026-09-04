"""progress — liveness/progress TRUTH (the moat, research-confirmed 2026-05-29).

Anthropic's Agent View equates "process alive" with "Working" and is structurally
blind to a SILENT HANG (process up, token count frozen). Every watchdog/heartbeat/
timeout/per-session-cost request was closed "not planned" (claude-code #37521,
#28482, #49150, #23250) — the lane is open. The ground-truth signal users
independently converged on is TOKEN-DELTA OVER TIME: if the token count is flat
while the session looks busy, the response has stalled.

This layer snapshots each session's output-token total over time and derives a
TRUE state the incumbent cannot: progressing / stalled / spiraling / idle. It
reads usage from transcripts, so it works on ANY session — including the operator's own
hand-started ones (where a silent hang is the real, unsolved pain), not just
Overseer-owned children. Call it repeatedly (daemon/poll) to build the history.
"""
from __future__ import annotations

import json
import time

from . import driver, usage

SNAP = driver.DATA / "token-snapshots.json"
STALL_SEC = 120.0                 # busy + output tokens flat this long -> stalled (silent hang)
SPIRAL_TOKENS_PER_MIN = 60_000    # sustained output burn above this -> spiraling (runaway)
RATE_WINDOW_SEC = 60.0            # compute the burn rate over this trailing window of samples
MAX_SAMPLES = 24                  # bound the per-session ring


def _samples_with(entry, now: float, tokens: int) -> list:
    """Migrate any prior snapshot entry to a [[ts, tokens], ...] ring and append the
    new reading. Accepts the old {tokens, since, seen} schema (seeds one sample) so a
    pre-existing snapshot file upgrades in place."""
    if isinstance(entry, dict) and isinstance(entry.get("samples"), list):
        s = [x for x in entry["samples"] if isinstance(x, list) and len(x) == 2]
    elif isinstance(entry, dict) and "tokens" in entry:
        s = [[entry.get("since", now), entry["tokens"]]]
    else:
        s = []
    s.append([now, tokens])
    return s


def _load() -> dict:
    try:
        return json.loads(SNAP.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save(snaps: dict) -> None:
    driver.DATA.mkdir(parents=True, exist_ok=True)
    tmp = SNAP.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snaps))
    tmp.replace(SNAP)


def classify(session_id: str, raw_status: str | None, now: float | None = None,
             snaps: dict | None = None, tokens: int | None = None) -> dict:
    """Derive progress truth for one session from its output-token delta over time.

    `snaps` (the persisted history) is mutated; pass `tokens` to inject a count in
    tests instead of reading the transcript. Returns
    {session_id, verdict, tokens, flat_for, rate_per_min}.
    """
    now = now if now is not None else time.time()
    if snaps is None:
        snaps = _load()
    if tokens is None:
        u = usage.session_usage(session_id)
        tokens = u["output"] if u else None
    raw = (raw_status or "").lower()

    prev = snaps.get(session_id)
    flat_for = 0.0
    rate = 0.0
    if tokens is None:
        verdict = "unknown"
    else:
        prev_tokens = prev.get("tokens") if isinstance(prev, dict) else None
        # 'since' (last token INCREASE) drives flat_for and is poll-INDEPENDENT: only
        # a real increase moves it, so any number of pollers compute the same flatness.
        if prev_tokens is None or tokens > prev_tokens:
            since = now
        else:
            since = prev.get("since", now)
        flat_for = now - since
        # Burn rate over a trailing WINDOW of samples — NOT consumed on read. The old
        # code recomputed the rate only on the poll that first saw each increase and
        # reset the cursor, so a second poller (the 24/7 guard daemon alongside the
        # app) saw 'flat' and diluted/missed the runaway. A sample ring is additive:
        # more pollers only sharpen the rate; whoever reads sees the same window.
        samples = _samples_with(prev, now, tokens)
        win = [s for s in samples if s[0] >= now - RATE_WINDOW_SEC] or samples[-2:]
        win = win[-MAX_SAMPLES:]
        o_ts, o_tok = win[0]
        n_ts, n_tok = win[-1]
        dt = n_ts - o_ts
        if dt >= 1.0 and n_tok > o_tok:
            rate = (n_tok - o_tok) / dt * 60.0
        snaps[session_id] = {"tokens": tokens, "since": since, "seen": now, "samples": win}
        # priority: stall (busy + flat + long) > spiraling (windowed burn) > progressing > idle
        if prev_tokens is None:
            verdict = "progressing" if raw == "busy" else "idle"
        elif raw == "busy" and tokens <= prev_tokens and flat_for >= STALL_SEC:
            verdict = "stalled"                                    # the silent hang Agent View can't see
        elif rate >= SPIRAL_TOKENS_PER_MIN:
            verdict = "spiraling"
        elif raw == "busy" or tokens > prev_tokens:
            verdict = "progressing"
        else:
            verdict = "idle"
    return {"session_id": session_id, "verdict": verdict, "tokens": tokens,
            "flat_for": round(flat_for, 1), "rate_per_min": round(rate)}


def _tail_for(session_id: str) -> list:
    """The transcript tail records for a session (or []). One read, reused below for
    BOTH the display title and loop detection (a separate seam so tests can mock it)."""
    from . import state
    t = state.find_transcript(session_id)
    if not t:
        return []
    try:
        return state._tail_records(t)
    except Exception:
        return []


def fleet_progress(leads=None, now: float | None = None) -> dict:
    """Classify every live session's progress truth; flag stalled / spiraling / looping."""
    now = now if now is not None else time.time()
    from . import roster, state
    if leads is None:
        try:
            leads = roster.fetch()
        except roster.RosterError:
            leads = []
    snaps = _load()
    rows = []
    for l in leads:
        r = classify(l.session_id, l.raw_status, now=now, snaps=snaps)
        records = _tail_for(l.session_id)
        # Name by the session's ai-title (not the shared cwd) so an alert can say WHICH
        # session is stalled/looping — otherwise every alert reads the shared cwd and
        # the whole 'which agent needs me' point dies on the alert surface (found in
        # dogfood 2026-05-30: the title fix had reached the board but not progress/alerts).
        r["name"] = l.name or state.session_title(records) or l.cwd or l.session_id[:8]
        # Loop overlay: a session that token-progresses (or stalls) while repeating the
        # SAME tool call is stuck looping — the state token-delta alone can't see (it can
        # burn tokens forever and look 'progressing'). Only checked for ACTIVE verdicts;
        # detect_loop is conservative (exact 3-in-8) so a legit retry won't false-fire.
        if r["verdict"] in ("progressing", "stalled") and records:
            loop_tool = state.detect_loop(records)
            if loop_tool:
                r["verdict"] = "looping"
                r["loop_tool"] = loop_tool
        rows.append(r)
    _save(snaps)
    flagged = [r for r in rows if r["verdict"] in ("stalled", "spiraling", "looping")]
    rows.sort(key=lambda r: {"stalled": 0, "looping": 1, "spiraling": 2, "progressing": 3,
                             "idle": 4, "unknown": 5}.get(r["verdict"], 6))
    return {"sessions": rows, "flagged": flagged,
            "summary": (", ".join(f"{r['name']}:{r['verdict']}" for r in flagged)
                        if flagged else "all sessions progressing or idle")}
