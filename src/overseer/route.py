"""route — the "manager" verb: say it once, Overseer delivers to the right worker.

The roadmap's intent routing (deferred as design-only until a delivery path
existed). Now that relay.tell can deliver safely to an owned, quiescent child, we
add the classifier in front of it — scoped to OWNED children (the safe set).

Dumb on purpose (per ROADMAP: "start dumb, not an LLM call — cheap & debuggable"):
score each owned worker by keyword/path overlap between the instruction and the
worker's (name + cwd + project dir). A clear, unique winner is routed there; a tie
or no-signal is AMBIGUOUS and we refuse to guess — mis-routing a command is worse
than asking the human to name the worker. Delivery reuses relay.tell, so the same
ownership + quiescence guards apply.
"""
from __future__ import annotations

import re

from . import driver, relay

_WORD = re.compile(r"[a-z0-9]+")
# generic path/dir tokens that carry no routing signal (every worker shares them)
_STOP = {"users", "tomtim", "home", "var", "tmp", "private", "the", "fix", "this",
         "that", "and", "for", "with", "please", "run", "make", "to", "a", "in",
         "on", "of", "it", "do", "add", "update", "check"}


def _tokens(s: str | None) -> set[str]:
    return {t for t in _WORD.findall((s or "").lower()) if t not in _STOP and len(t) > 1}


def _worker_tokens(name: str, meta: dict) -> set[str]:
    toks = _tokens(name)
    cwd = meta.get("cwd") or ""
    toks |= _tokens(cwd)
    if cwd:
        toks |= _tokens(cwd.rstrip("/").split("/")[-1])  # project dir = most identifying
    return toks


def score(instruction: str, registry: dict | None = None) -> list[dict]:
    """[{name, score, overlap}] for every owned worker, best first."""
    itoks = _tokens(instruction)
    reg = registry if registry is not None else driver._load_registry()
    out = []
    for name, meta in reg.items():
        overlap = sorted(itoks & _worker_tokens(name, meta))
        out.append({"name": name, "score": len(overlap), "overlap": overlap})
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def classify(instruction: str, registry: dict | None = None) -> tuple[str | None, list[dict]]:
    """(chosen_name | None, scored). chosen only on a clear, unique winner."""
    scored = score(instruction, registry)
    if not scored or scored[0]["score"] == 0:
        return None, scored                          # no signal -> ambiguous
    if len(scored) == 1 or scored[0]["score"] > scored[1]["score"]:
        return scored[0]["name"], scored             # strict winner
    return None, scored                              # tie -> refuse to guess


def route(instruction: str, go: bool = False, force: bool = False,
          registry: dict | None = None) -> dict:
    """Classify, then (if go) deliver via relay.tell. Dry-run by default."""
    if not (instruction and instruction.strip()):
        return {"routed": False, "reason": "empty instruction", "candidates": []}
    chosen, scored = classify(instruction, registry)
    plan = {"instruction": instruction, "chosen": chosen, "candidates": scored}
    if not chosen:
        plan["routed"] = False
        plan["reason"] = ("ambiguous — no clear owner. Name the worker explicitly: "
                          "overseer tell <name> ...")
        return plan
    if not go:
        plan["routed"] = False
        plan["reason"] = f"dry-run — would deliver to '{chosen}' (pass --go to send)"
        return plan
    try:
        plan["delivery"] = relay.tell(chosen, instruction, force=force)
        plan["routed"] = True
    except relay.RelayError as e:
        plan["routed"] = False
        plan["reason"] = f"delivery refused: {e}"
    return plan
