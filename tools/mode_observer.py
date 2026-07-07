#!/usr/bin/env python3
"""mode_observer — W1/T2.4 inkrement 2: shadow-only obserwator „would-be-mode".

Mode to GLOBALNY stan (nie per-zlecenie) → próbkuje żywy świat co tick, przepuszcza
przez PRODUKCYJNY FSM `mode_layer.step` (persistent ModeState w pliku) i loguje
would-be-mode + reason + sygnały do jsonl. ZERO wpływu na decyzje (nie dotyka
scoringu/feasibility/serializera decyzji; osobny plik logu).

To jest fundament „shadow 7 d would-be-mode" ze spec A2 — PRZED jakimkolwiek flipem.
NIE instaluje timera (to ACK Adriana). Uruchom ręcznie / jako timer po ACK:
  venvs/dispatch/bin/python -m dispatch_v2.tools.mode_observer --once

Źródła (read-only): orders_state.json (in-flight/latency), pending_pool.json (kolejka).
Stan FSM: dispatch_state/mode_observer_state.json (atomowy zapis). Log: mode_observer.jsonl.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

SCRIPTS = "/root/.openclaw/workspace/scripts"
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from dispatch_v2 import mode_layer as M  # noqa: E402

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
ORDERS_STATE = f"{STATE_DIR}/orders_state.json"
PENDING_POOL = f"{STATE_DIR}/pending_pool.json"
FSM_STATE = f"{STATE_DIR}/mode_observer_state.json"
LOG = f"{STATE_DIR}/mode_observer.jsonl"


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _pending_count(pool):
    if isinstance(pool, list):
        return len(pool)
    if isinstance(pool, dict):
        return len(pool.get("orders", pool))
    return 0


def read_current_mode(fsm_state_path=FSM_STATE):
    """(mode, reason) z pliku stanu obserwatora — do STEMPLA decyzji (read-only,
    NIE krok FSM; obserwator jest jedynym, który krokuje). Fail-soft → (S1, 'no-state')
    gdy plik brak/uszkodzony (bezpieczny default: tryb spokojny)."""
    d = _load_json(fsm_state_path, None)
    if isinstance(d, dict) and d.get("mode"):
        return str(d["mode"]), str(d.get("reason") or "")
    return M.S1, "no-state"


def _load_state(fsm_state_path=FSM_STATE):
    d = _load_json(fsm_state_path, None)
    if isinstance(d, dict) and "mode" in d:
        return M.ModeState(mode=d.get("mode", M.S1),
                           entered_at_min=d.get("entered_at_min", 0.0),
                           two_of_three_since_min=d.get("two_of_three_since_min"),
                           reason=d.get("reason", "init"))
    return M.ModeState(mode=M.S1)


def _atomic_dump(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def observe_once(now=None, orders_path=ORDERS_STATE, pending_path=PENDING_POOL,
                 fsm_state_path=FSM_STATE, log_path=LOG):
    now = now or datetime.now(timezone.utc)
    orders = _load_json(orders_path, {})
    pending = _pending_count(_load_json(pending_path, []))
    sig = M.mode_signals_from_state(orders, now, pending_count=pending)
    state = _load_state(fsm_state_path)
    new = M.step(state, sig)
    rec = {
        "ts": now.isoformat(timespec="seconds"),
        "mode": new.mode, "prev_mode": state.mode, "reason": new.reason,
        "transition": new.mode != state.mode,
        "signals": {"L": sig.load_inflight_per_active, "queue": sig.queue_pending,
                    "latency_med_min": sig.assign_latency_med_min},
    }
    _atomic_dump(fsm_state_path, {**dataclasses.asdict(new)})
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="jeden pomiar (do timera/ręcznie)")
    a = ap.parse_args(argv)
    rec = observe_once()
    print(json.dumps(rec, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
