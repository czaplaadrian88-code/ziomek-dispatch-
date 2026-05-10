#!/usr/bin/env python3
"""TASK A czasowka observability monitor — multi-source live poll.

Standalone draft script (NIE prod kod). READ-ONLY na prod data.

Sources (all read-only):
 1. czasowka_proposals_state.json — orders dict + triggers_fired + final_assignments
 2. candidate_decisions_<YYYY-MM-DD>.jsonl — filter source="czasowka_proactive"
 3. learning_log.jsonl tail — filter event in {CZASOWKA_PROPOSAL, FLAG_FLIP_TASK_A, ...}
 4. journalctl dispatch-czasowka.service — last 5 min, ERROR/WARN
 5. journalctl dispatch-telegram.service — last 5 min, ERROR/WARN

CLI:
  python czasowka_observability_monitor.py --watch     # poll co 30s
  python czasowka_observability_monitor.py --snapshot  # one-shot

Output: human-readable formatted blocks per detected change.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

STATE_PATH = Path("/root/.openclaw/workspace/dispatch_state/czasowka_proposals_state.json")
OBS_DIR = Path("/root/.openclaw/workspace/dispatch_state/observability")
LEARNING_LOG = Path("/root/.openclaw/workspace/dispatch_state/learning_log.jsonl")
LEARNING_TAIL_LINES = 100
LEARNING_EVENTS = {
    "CZASOWKA_PROPOSAL",
    "CZASOWKA_TRIGGER_FIRE",
    "CZASOWKA_DECISION",
    "FLAG_FLIP_TASK_A",
}
JOURNAL_SERVICES = ("dispatch-czasowka.service", "dispatch-telegram.service")
JOURNAL_WINDOW = "5 minutes ago"
POLL_INTERVAL_SEC = 30
SEP = "=" * 63


# ─── helpers ──────────────────────────────────────────────────────────────────


def _now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _today_yyyymmdd() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _read_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"orders": {}, "_missing": True}
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"orders": {}, "_corrupt": True}
        data.setdefault("orders", {})
        return data
    except (json.JSONDecodeError, OSError) as exc:
        return {"orders": {}, "_error": str(exc)}


def _candidate_decisions_path() -> Path:
    return OBS_DIR / f"candidate_decisions_{_today_yyyymmdd()}.jsonl"


def _read_candidate_decisions_czasowka() -> List[Dict[str, Any]]:
    """Return all candidate_decisions entries z source=='czasowka_proactive' for today."""
    p = _candidate_decisions_path()
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                src = str(d.get("source", ""))
                if "czasowka_proactive" in src:
                    out.append(d)
    except OSError:
        pass
    return out


def _tail_learning_log(limit: int = LEARNING_TAIL_LINES) -> List[Dict[str, Any]]:
    if not LEARNING_LOG.exists():
        return []
    try:
        with open(LEARNING_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 200_000)
            f.seek(size - chunk)
            tail = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for line in tail[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("event") in LEARNING_EVENTS:
            out.append(d)
    return out


def _journalctl_errors(service: str, since: str = JOURNAL_WINDOW) -> List[str]:
    try:
        proc = subprocess.run(
            ["journalctl", "-u", service, "--since", since, "--no-pager", "-q"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return [f"<journalctl unavailable: {exc!r}>"]
    if proc.returncode != 0:
        return []
    out: List[str] = []
    for line in proc.stdout.splitlines():
        low = line.lower()
        if " error" in low or " err " in low or "warning" in low or " warn " in low:
            out.append(line)
    return out


# ─── snapshot signature (dla watcher diff detect) ─────────────────────────────


def _signature(state: Dict[str, Any], cands: List[Dict[str, Any]],
               learning: List[Dict[str, Any]]) -> Tuple[Any, ...]:
    orders = state.get("orders", {})
    fingerprint_state = (
        len(orders),
        tuple(sorted(orders.keys())),
        tuple(
            (oid, len(o.get("triggers_fired", {}) or {}),
             o.get("final_assignment_cid"))
            for oid, o in sorted(orders.items())
        ),
    )
    last_cand_ts = cands[-1].get("ts") if cands else None
    last_learn_ts = learning[-1].get("ts") if learning else None
    return (fingerprint_state, len(cands), last_cand_ts, len(learning), last_learn_ts)


# ─── render ───────────────────────────────────────────────────────────────────


def _fmt_order_block(oid: str, rec: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    rest = rec.get("restaurant") or "?"
    addr = rec.get("delivery_address") or "?"
    city = rec.get("delivery_city") or ""
    czas_pickup = rec.get("czas_odbioru_ts") or "?"
    triggers = rec.get("triggers_fired") or {}
    final_cid = rec.get("final_assignment_cid")
    final_ts = rec.get("final_assignment_ts")
    excluded = rec.get("excluded_candidates") or []
    addr_full = f"{addr}, {city}".strip(", ") if city else addr
    lines.append(f"  Order #{oid} ({rest})")
    lines.append(f"     Pickup {czas_pickup} -> {addr_full}")
    if triggers:
        for trig_name in sorted(triggers.keys(), key=lambda k: -int(k) if str(k).isdigit() else 0):
            t = triggers[trig_name]
            ts = t.get("ts", "?")
            cid = t.get("proposed_cid")
            name = t.get("proposed_name") or "?"
            score = t.get("score")
            decision = t.get("decision") or "PENDING"
            decision_ts = t.get("decision_ts")
            score_repr = f"{score}" if score is not None else "n/a"
            lines.append(
                f"     T-{trig_name} fired @ {ts}  proposed={name} (cid={cid}, score={score_repr})"
            )
            line2 = f"        decision={decision}"
            if decision_ts:
                line2 += f" @ {decision_ts}"
            lines.append(line2)
    else:
        lines.append("     (no triggers fired yet)")
    if final_cid:
        lines.append(f"     FINAL ASSIGNMENT cid={final_cid} @ {final_ts}")
    if excluded:
        lines.append(f"     excluded_candidates={excluded}")
    return lines


def _render(state: Dict[str, Any], cands: List[Dict[str, Any]],
            learning: List[Dict[str, Any]],
            journals: Dict[str, List[str]]) -> str:
    out: List[str] = []
    out.append(f"[{_now_utc_str()}] CZASOWKA OBSERVABILITY SNAPSHOT")
    out.append(SEP)

    # Source 1: state file
    out.append("[1] czasowka_proposals_state.json")
    if state.get("_missing"):
        out.append("    (file does not exist yet — 0 orders)")
    elif state.get("_corrupt"):
        out.append("    (CORRUPT — top-level not dict)")
    elif state.get("_error"):
        out.append(f"    (READ ERROR: {state['_error']})")
    else:
        orders = state.get("orders", {}) or {}
        out.append(f"    orders_count={len(orders)}  updated_at={state.get('updated_at')}")
        for oid in sorted(orders.keys()):
            out.extend(_fmt_order_block(oid, orders[oid]))
    out.append("")

    # Source 2: candidate_decisions czasowka
    out.append("[2] candidate_decisions_<today>.jsonl  (source=czasowka_proactive)")
    if not cands:
        out.append("    0 czasowka_proactive entries today")
    else:
        out.append(f"    {len(cands)} czasowka_proactive entries today")
        for c in cands[-5:]:
            ts = c.get("ts")
            oid = c.get("order_id")
            ctx = c.get("context") or {}
            decision = c.get("decision")
            # decision is dict z verdict + decision_threshold w naszym schemacie
            verdict = "?"
            best_cid = None
            best_score = None
            threshold = None
            if isinstance(decision, dict):
                verdict = decision.get("verdict", "?")
                best_cid = decision.get("best_candidate_cid")
                best_score = decision.get("best_score")
                threshold = decision.get("decision_threshold")
            else:
                verdict = str(decision) if decision else "?"
            # extract trigger_min z threshold ('czasowka_proactive_t50' -> 'T-50')
            trig = ctx.get("trigger_min") or ctx.get("trigger")
            if not trig and threshold and threshold.startswith("czasowka_proactive_t"):
                trig = threshold.split("_t", 1)[1]
            trig_str = f"T-{trig}" if trig else "T-?"
            n_cands = c.get("candidates_evaluated_count")
            best_str = (
                f"cid={best_cid} score={best_score}" if best_cid else "(none)"
            )
            out.append(
                f"    [{ts}] oid={oid} {trig_str} verdict={verdict} "
                f"n_cands={n_cands} best={best_str}"
            )
    out.append("")

    # Source 3: learning_log tail
    out.append(f"[3] learning_log.jsonl tail (last {LEARNING_TAIL_LINES} lines, "
               f"events={sorted(LEARNING_EVENTS)})")
    if not learning:
        out.append("    no matching events in tail window")
    else:
        out.append(f"    {len(learning)} matching events:")
        for e in learning[-10:]:
            ts = e.get("ts")
            ev = e.get("event")
            extras: List[str] = []
            for k in ("flag", "new_value", "order_id", "decision", "trigger"):
                if k in e:
                    extras.append(f"{k}={e[k]}")
            out.append(f"    [{ts}] {ev}  {' '.join(extras)}")
    out.append("")

    # Source 4+5: journalctl
    for svc in JOURNAL_SERVICES:
        out.append(f"[journal] {svc}  (last {JOURNAL_WINDOW})")
        lines = journals.get(svc) or []
        if not lines:
            out.append("    0 ERROR/WARN")
        else:
            out.append(f"    {len(lines)} ERROR/WARN lines:")
            for ln in lines[-8:]:
                out.append(f"    > {ln}")
        out.append("")

    out.append(SEP)
    return "\n".join(out)


def _collect() -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]],
                        Dict[str, List[str]]]:
    state = _read_state()
    cands = _read_candidate_decisions_czasowka()
    learning = _tail_learning_log()
    journals = {svc: _journalctl_errors(svc) for svc in JOURNAL_SERVICES}
    return state, cands, learning, journals


# ─── modes ────────────────────────────────────────────────────────────────────


def cmd_snapshot() -> int:
    state, cands, learning, journals = _collect()
    print(_render(state, cands, learning, journals))
    return 0


def cmd_watch() -> int:
    last_sig: Optional[Tuple[Any, ...]] = None
    print(f"[{_now_utc_str()}] watch mode start (poll {POLL_INTERVAL_SEC}s) — "
          f"output on detected change. Ctrl-C to stop.", flush=True)
    while True:
        try:
            state, cands, learning, journals = _collect()
            sig = _signature(state, cands, learning)
            if sig != last_sig:
                print(_render(state, cands, learning, journals), flush=True)
                last_sig = sig
            time.sleep(POLL_INTERVAL_SEC)
        except KeyboardInterrupt:
            print(f"\n[{_now_utc_str()}] watch stopped (KeyboardInterrupt)", flush=True)
            return 0
        except Exception as exc:
            print(f"[{_now_utc_str()}] watch loop error: {exc!r}", flush=True)
            time.sleep(POLL_INTERVAL_SEC)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="TASK A czasowka observability monitor")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--watch", action="store_true", help="poll co 30s, print on change")
    g.add_argument("--snapshot", action="store_true", help="one-shot snapshot")
    args = p.parse_args(argv)
    if args.watch:
        return cmd_watch()
    return cmd_snapshot()


if __name__ == "__main__":
    sys.exit(main())
