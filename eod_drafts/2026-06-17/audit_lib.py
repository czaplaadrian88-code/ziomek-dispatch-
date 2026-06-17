#!/usr/bin/env python3
"""
Shared READ-ONLY audit engine for the Ziomek hard-cases counterfactual audit (7d, 2026-06-10..16).
Used by analyze.py (inline) and by workflow sub-agents. No side effects on import.

Core idea: shadow_decisions.alternatives[] logs every FEASIBLE candidate's full objective
breakdown at decision time. We compare the CHOSEN `best` against each logged alternative on the
TRUE objective axes (R6 max-bag delivery, committed-pickup breach, new-order pickup lateness,
#orders breached), guarded by position-reliability and feasibility — a conservative Pareto
dominance test. If an alternative strictly dominates the chosen best -> Ziomek left a strictly
better feasible option on the table (decision-time, not hindsight).
"""
import json, os, urllib.request
from datetime import datetime, timedelta

WS = "/root/.openclaw/workspace"
BACKFILL = f"{WS}/dispatch_state/backfill_decisions_outcomes_v1.jsonl"
CAPTURE  = f"{WS}/dispatch_state/obj_replay_capture.jsonl"
SHADOW   = f"{WS}/scripts/logs/shadow_decisions.jsonl"
KOORD    = f"{WS}/dispatch_state/auto_koord_log.jsonl"
SCRATCH  = f"{WS}/scripts/dispatch_v2/eod_drafts/2026-06-17"
OSRM     = "http://127.0.0.1:5001"

WIN_LO, WIN_HI = "2026-06-10", "2026-06-16"
EPS = 1.5  # minutes tolerance to avoid noise-level "improvements"

# position-source reliability: lower = more trustworthy.
# no_gps/blind/None = fictional BIALYSTOK_CENTER position (V3.16 demote rationale) -> WORST (3).
# pre_shift = real shift-start location but stale -> 2.  derived-from-activity -> 1.  live gps -> 0.
POS_REL = {
    "gps": 0,
    "last_picked_up_recent": 1, "last_picked_up_pickup": 1, "last_picked_up_delivery": 1,
    "last_assigned_pickup": 1, "last_assigned_delivery": 1, "post_wave": 1, "pos_from_store": 1,
    "last_known": 1, "last_delivered": 1,
    "pre_shift": 2,
    "no_gps": 3, "blind": 3, None: 3, "": 3,
}
def pos_rel(src): return POS_REL.get(src, 3)

def parse_ts(s):
    if not s: return None
    try: return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception: return None

def wday(ts):
    d = parse_ts(ts)
    return (d + timedelta(hours=2)).strftime("%Y-%m-%d") if d else None

def inwin(ts):
    d = wday(ts)
    return bool(d and WIN_LO <= d <= WIN_HI)

def num(x, default=None):
    try:
        if x is None: return default
        return float(x)
    except Exception:
        return default

def oid_int(oid):
    try: return int(str(oid))
    except Exception: return -1

def e2_signature(oid):
    """E2-pln pure-resort bug only affected order_id % 5 == 0 (excluded set; verify coverage)."""
    n = oid_int(oid)
    return n >= 0 and n % 5 == 0

# ---- candidate slimming -------------------------------------------------------
CAND_FIELDS = [
    "courier_id","name","score","feasibility","reason","best_effort","pos_source","pos_from_store",
    "pos_age_min","km_to_pickup","drive_min","travel_min","travel_min_cal","time_to_pickup_ready_min",
    "free_at_min","r6_max_bag_time_min","r6_worst_oid","r6_is_solo","r6_bag_size","bag_size_before",
    "objm_r6_breach_max_min","objm_r6_breach_count","objm_route_span_min",
    "late_pickup_committed_breach","late_pickup_committed_max","late_pickup_committed_worst_oid",
    "new_pickup_late_min","new_pickup_needs_extension","late_pickup_max_min",
    "eta_pickup_hhmm","eta_drive_hhmm","czas_kuriera_hhmm","is_coordinator","new_courier_ramp",
]
def slim_cand(c, is_best=False):
    d = {k: c.get(k) for k in CAND_FIELDS if k in c}
    d["is_best"] = is_best
    return d

def slim_record(r):
    best = r.get("best") or {}
    alts = r.get("alternatives") or []
    cands = [slim_cand(best, True)] + [slim_cand(a, False) for a in alts]
    return {
        "order_id": str(r.get("order_id")),
        "ts": r.get("ts"),
        "day": wday(r.get("ts")),
        "verdict": r.get("verdict"),
        "restaurant": r.get("restaurant"),
        "delivery_address": r.get("delivery_address"),
        "address_id": r.get("address_id"),
        "auto_route": r.get("auto_route"),
        "auto_route_reason": r.get("auto_route_reason"),
        "reason": r.get("reason"),
        "pool_total": r.get("pool_total_count"),
        "pool_feas": r.get("pool_feasible_count"),
        "pickup_ready_at": r.get("pickup_ready_at"),
        "pickup_at_warsaw": r.get("pickup_at_warsaw"),
        "order_created_at": r.get("order_created_at"),
        "cands": cands,
    }

# ---- dominance ----------------------------------------------------------------
def feas_rank(c):
    f = str(c.get("feasibility") or "").upper()
    return {"YES": 0, "MAYBE": 1}.get(f, 2)  # NO/other = 2

R6_ARTIFACT_MIN = 300.0  # r6_max_bag beyond this = zombie/stale picked_up_at poisoning, not real

def _g(c, k):  # numeric getter with safe default for "less is better" axes
    v = num(c.get(k))
    return v

def dominates(A, B):
    """Return (True, deltas) iff candidate A strictly Pareto-dominates B on the true objective,
    guarded by reliability+feasibility. Conservative: A must be >= on every axis, strictly better
    on >=1, and A's position must be at least as trustworthy (so pre_shift fantasy can't 'win')."""
    a_r6, b_r6 = _g(A, "r6_max_bag_time_min"), _g(B, "r6_max_bag_time_min")
    if a_r6 is None or b_r6 is None:
        return (False, None)
    a_cm = _g(A, "late_pickup_committed_max") or 0.0
    b_cm = _g(B, "late_pickup_committed_max") or 0.0
    a_nl = _g(A, "new_pickup_late_min") or 0.0
    b_nl = _g(B, "new_pickup_late_min") or 0.0
    a_bc = _g(A, "objm_r6_breach_count") or 0.0
    b_bc = _g(B, "objm_r6_breach_count") or 0.0
    a_rel, b_rel = pos_rel(A.get("pos_source")), pos_rel(B.get("pos_source"))
    a_fr, b_fr = feas_rank(A), feas_rank(B)

    # guards: A at least as trustworthy & at least as feasible
    if a_rel > b_rel: return (False, None)
    if a_fr > b_fr:   return (False, None)
    # A no worse on every minute/count axis
    if not (a_r6 <= b_r6 + EPS and a_cm <= b_cm + EPS and a_nl <= b_nl + EPS and a_bc <= b_bc):
        return (False, None)
    # strictly better on at least one axis
    strict = (a_r6 < b_r6 - EPS or a_cm < b_cm - EPS or a_nl < b_nl - EPS
              or a_bc < b_bc or a_fr < b_fr)
    if not strict:
        return (False, None)
    deltas = {
        "d_r6_max_bag": round(b_r6 - a_r6, 2),
        "d_committed_max": round(b_cm - a_cm, 2),
        "d_new_pickup_late": round(b_nl - a_nl, 2),
        "d_breach_count": b_bc - a_bc,
        "A_rel": a_rel, "B_rel": b_rel, "A_feas": a_fr, "B_feas": b_fr,
    }
    return (True, deltas)

def analyze_decision(slim):
    """Return dict: chosen best vs alternatives. Flags strict-dominators of the chosen best."""
    cands = slim["cands"]
    if not cands: return None
    best = cands[0]
    alts = cands[1:]
    dominators = []
    for i, a in enumerate(alts):
        ok, deltas = dominates(a, best)
        if ok:
            dominators.append({"alt_idx": i, "cid": a.get("courier_id"), "name": a.get("name"),
                                "score": a.get("score"), "pos_source": a.get("pos_source"),
                                "deltas": deltas,
                                "r6_max_bag": a.get("r6_max_bag_time_min"),
                                "committed_max": a.get("late_pickup_committed_max"),
                                "new_pickup_late": a.get("new_pickup_late_min"),
                                "feasibility": a.get("feasibility"),
                                "is_coordinator": a.get("is_coordinator"),
                                "new_courier_ramp": a.get("new_courier_ramp"),
                                "reason": a.get("reason")})
    # score-top among all candidates
    scored = [(num(c.get("score")), idx) for idx, c in enumerate(cands) if num(c.get("score")) is not None]
    score_top_idx = max(scored)[1] if scored else 0
    best_is_score_top = (score_top_idx == 0)
    return {
        "best_cid": best.get("courier_id"),
        "best_score": best.get("score"),
        "best_r6": best.get("r6_max_bag_time_min"),
        "best_committed_max": best.get("late_pickup_committed_max"),
        "best_committed_breach": best.get("late_pickup_committed_breach"),
        "best_new_late": best.get("new_pickup_late_min"),
        "best_pos_source": best.get("pos_source"),
        "best_feasibility": best.get("feasibility"),
        "best_effort": best.get("best_effort"),
        "n_alts": len(alts),
        "best_is_score_top": best_is_score_top,
        "score_top_cid": cands[score_top_idx].get("courier_id"),
        "dominators": dominators,
    }

# ---- loaders ------------------------------------------------------------------
def load_backfill_window():
    rows = []
    with open(BACKFILL) as f:
        for line in f:
            if not line.strip(): continue
            try: r = json.loads(line)
            except Exception: continue
            if inwin(r.get("decision_ts")):
                rows.append(r)
    return rows

def build_slim_shadow_index(save_path=None):
    """Stream shadow_decisions once -> {order_id: [slim_record,...]} for window. Optionally save JSON."""
    idx = {}
    with open(SHADOW) as f:
        for line in f:
            if not line.strip(): continue
            try: r = json.loads(line)
            except Exception: continue
            if not inwin(r.get("ts")): continue
            sr = slim_record(r)
            idx.setdefault(sr["order_id"], []).append(sr)
    if save_path:
        with open(save_path, "w") as g:
            json.dump(idx, g, ensure_ascii=False)
    return idx

def load_capture_for(order_ids):
    """Stream capture -> {order_id: [rows]} for the requested ids (chosen-courier geometry)."""
    want = set(str(x) for x in order_ids)
    out = {}
    with open(CAPTURE) as f:
        for line in f:
            if not line.strip(): continue
            try: r = json.loads(line)
            except Exception: continue
            if str(r.get("order_id")) in want and inwin(r.get("ts")):
                out.setdefault(str(r.get("order_id")), []).append(r)
    return out

# ---- OSRM (read-only; batch via /table) --------------------------------------
def osrm_route(a, b):
    """a,b = (lat,lng). Returns (duration_min, distance_km) or (None,None)."""
    url = f"{OSRM}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=false"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            d = json.load(resp)
        rt = d["routes"][0]
        return (rt["duration"]/60.0, rt["distance"]/1000.0)
    except Exception:
        return (None, None)

def osrm_table(coords):
    """coords = list of (lat,lng). Returns NxN duration matrix in minutes (or None)."""
    pts = ";".join(f"{c[1]},{c[0]}" for c in coords)
    url = f"{OSRM}/table/v1/driving/{pts}?annotations=duration"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            d = json.load(resp)
        return [[(v/60.0 if v is not None else None) for v in row] for row in d["durations"]]
    except Exception:
        return None

if __name__ == "__main__":
    print("audit_lib self-test")
    print("OSRM route test:", osrm_route((53.1324,23.1508),(53.1635,23.2026)))
