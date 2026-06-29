#!/usr/bin/env python3
"""
N2 wait-courier fix — DOWODOWY replay (READ-ONLY, 2026-06-17 data only).

Root cause: dispatch_pipeline.py:3728 `_bag_size_at_insertion_273 = len(bag_sim)` counts the WHOLE
bag (assigned + picked_up). scoring.compute_wait_courier_penalty hard-rejects when
bag_size_at_insertion>=1 and wait_min>15. Docstring intent: ALREADY-PICKED-UP food cools while the
courier idles. Bug: counts assigned, not picked-up.

Fix variant under test: bag_size_at_insertion = #orders in bag with picked_up_at != None.
Empty-picked-up courier (0 picked up) -> "cooling food" rule does NOT fire.

This harness re-derives, from obj_replay_capture.jsonl (the solver INPUT log), the wait-courier
gate exactly as dispatch_pipeline.py computes it:
  - bag_size_at_insertion (OLD = len(bag); NEW = #picked_up)
  - the per-pickup wait loop (lines 3729-3753): wait only for pickups whose ready time is known,
    and for BAG orders ONLY when picked_up_at is None (already-picked-up bag orders contribute no wait)
  - the verdict gate (lines 4812-4819): hard_reject -> NO ONLY when bag has a pending pickup
    (any picked_up_at is None) OR free-courier-skip disabled.

We compute plan.arrival_at[oid] via OSRM from courier_pos through the bag pickups (chain-aware,
greedy nearest like the real planner's effect on the NEW order's arrival). For the gate we need the
MAX wait across pickups; we compute arrival at each pending pickup (incl. the new order) and take wait.
OSRM read-only via audit_lib.
"""
import json, sys
from datetime import datetime, timedelta
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import audit_lib as AL

CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
DAY = "2026-06-17"

# --- constants mirrored from common.py (LIVE values) ---
WAIT_THRESHOLD_MIN = 3.0
WAIT_HARD_REJECT_MIN = 15.0
DWELL_PICKUP_DEFAULT = 2.0
DWELL_DROPOFF_DEFAULT = 2.0
SKIP_FREE_COURIER = True  # ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP default ON


def parse(s):
    return AL.parse_ts(s)


def load_today_capture():
    recs = []
    with open(CAPTURE) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts = r.get("ts")
            d = AL.wday(ts)
            if d == DAY:
                recs.append(r)
    return recs


def chain_arrivals(courier_pos, now_dt, pickups, dwell_pickup):
    """Greedy nearest-neighbour pickup ordering from courier_pos (OSRM minutes).
    pickups = list of dicts {oid, coords, ready_dt}. Returns {oid: arrival_dt} for each pickup,
    accumulating dwell at each visited pickup (mirrors route_simulator dwell add).
    Cooling/wait is computed against arrival (NOT departure)."""
    arrivals = {}
    remaining = list(pickups)
    cur = tuple(courier_pos)
    t = now_dt
    while remaining:
        # nearest by OSRM duration
        best_i, best_dur = None, None
        for i, p in enumerate(remaining):
            dur, _ = AL.osrm_route(cur, tuple(p["coords"]))
            if dur is None:
                dur = 1e9
            if best_dur is None or dur < best_dur:
                best_dur, best_i = dur, i
        p = remaining.pop(best_i)
        t = t + timedelta(minutes=best_dur)
        arrivals[p["oid"]] = t
        # courier then waits if early, then dwells; departure = max(arrival, ready) + dwell
        ready = p.get("ready_dt")
        dep_base = t if (ready is None or ready <= t) else ready
        t = dep_base + timedelta(minutes=dwell_pickup)
        cur = tuple(p["coords"])
    return arrivals


def compute_wait_gate(rec, picked_up_counts_as_insertion):
    """Re-derive wait-courier gate for ONE capture record (one candidate-courier for one new order).
    Returns dict with max_wait, hard_reject, gate_fires (verdict->NO), bag_size_used, has_pending_pickup.
    picked_up_counts_as_insertion: if True -> NEW fix (count only picked_up); if False -> OLD (len bag)."""
    bag = rec.get("bag") or []
    new_order = rec.get("new_order") or {}
    courier_pos = rec.get("courier_pos")
    now_dt = parse(rec.get("now"))
    dwell_pickup = AL.num(rec.get("dwell_pickup"), DWELL_PICKUP_DEFAULT) or DWELL_PICKUP_DEFAULT

    # bag_size_at_insertion
    n_picked = sum(1 for b in bag if b.get("picked_up_at"))
    n_total = len(bag)
    bag_size = n_picked if picked_up_counts_as_insertion else n_total

    has_pending = any(b.get("picked_up_at") is None for b in bag)

    # Build pickup list for arrival computation: new order pickup + bag PENDING pickups (picked_up_at None).
    # (mirror lines 3732-3737: new -> pickup_ready_at; bag order contributes ready ONLY if picked_up_at None)
    pickups = []
    no_ready = parse(new_order.get("pickup_ready_at"))
    if new_order.get("pickup_coords") and no_ready is not None:
        pickups.append({"oid": str(new_order.get("order_id")), "coords": new_order["pickup_coords"], "ready_dt": no_ready, "is_new": True})
    for b in bag:
        if b.get("picked_up_at") is None and b.get("pickup_coords"):
            br = parse(b.get("pickup_ready_at"))
            pickups.append({"oid": str(b.get("order_id")), "coords": b["pickup_coords"], "ready_dt": br, "is_new": False})

    if not pickups or courier_pos is None or now_dt is None:
        return {"max_wait": 0.0, "hard_reject": False, "gate_fires": False, "bag_size_used": bag_size,
                "n_picked": n_picked, "n_total": n_total, "has_pending": has_pending, "wait_oid": None}

    arrivals = chain_arrivals(courier_pos, now_dt, pickups, dwell_pickup)

    # wait per pickup = max(0, ready - arrival); track max
    max_wait, wait_oid = 0.0, None
    per = []
    for p in pickups:
        arr = arrivals.get(p["oid"])
        ready = p.get("ready_dt")
        if arr is None or ready is None:
            continue
        w = max(0.0, (ready - arr).total_seconds() / 60.0)
        per.append((p["oid"], round(w, 2), p["is_new"]))
        if w > max_wait:
            max_wait, wait_oid = w, p["oid"]

    # compute_wait_courier_penalty hard_reject logic:
    # bag_size<1 -> (0,False); wait<=threshold -> (0,False); wait>hard_reject_min -> (0,True)
    hard_reject = False
    if bag_size >= 1 and max_wait > WAIT_THRESHOLD_MIN and max_wait > WAIT_HARD_REJECT_MIN:
        hard_reject = True

    # verdict gate (lines 4812-4819): only if hard_reject and (has_pending OR not skip_free)
    gate_fires = hard_reject and (has_pending or not SKIP_FREE_COURIER)

    return {"max_wait": round(max_wait, 2), "hard_reject": hard_reject, "gate_fires": gate_fires,
            "bag_size_used": bag_size, "n_picked": n_picked, "n_total": n_total,
            "has_pending": has_pending, "wait_oid": wait_oid, "per": per}


def is_cid413_target(rec):
    bag = rec.get("bag") or []
    return (rec.get("tier") == "gold" and len(bag) == 1
            and str(bag[0].get("order_id")) == "481408"
            and rec.get("courier_pos") == [53.1325, 23.1688])


if __name__ == "__main__":
    print("loading today's capture ...")
    recs = load_today_capture()
    print(f"today ({DAY}) capture records: {len(recs)}\n")

    # ---------- CASE PROOF: 481410 / cid413 ----------
    print("=" * 70)
    print("CASE PROOF: oid=481410, candidate cid=413 (gold, bag=[481408 assigned, not picked up])")
    print("=" * 70)
    target = [r for r in recs if str(r.get("order_id")) == "481410" and is_cid413_target(r)]
    if not target:
        print("!! target capture record NOT found")
    for r in target:
        b0 = (r.get("bag") or [])[0]
        no = r.get("new_order") or {}
        print(f"ts={r.get('ts')} now={r.get('now')} courier_pos={r.get('courier_pos')}")
        print(f"  bag[0]: oid={b0.get('order_id')} status={b0.get('status')} picked_up_at={b0.get('picked_up_at')} ready={b0.get('pickup_ready_at')} pickup={b0.get('pickup_coords')}")
        print(f"  new   : oid={no.get('order_id')} ready={no.get('pickup_ready_at')} pickup={no.get('pickup_coords')} delivery={no.get('delivery_coords')}")
        old = compute_wait_gate(r, picked_up_counts_as_insertion=False)
        new = compute_wait_gate(r, picked_up_counts_as_insertion=True)
        print("\n  --- per-pickup wait (OSRM chain-aware arrival) ---")
        for oid, w, isnew in (old.get("per") or []):
            print(f"      {'NEW ' if isnew else 'bag '}{oid}: wait={w}min")
        print(f"\n  TODAY  (len bag) : bag_size_used={old['bag_size_used']}  max_wait={old['max_wait']}min  hard_reject={old['hard_reject']}  has_pending={old['has_pending']}  -> verdict-gate NO = {old['gate_fires']}")
        print(f"  FIXED (picked_up): bag_size_used={new['bag_size_used']}  max_wait={new['max_wait']}min  hard_reject={new['hard_reject']}  has_pending={new['has_pending']}  -> verdict-gate NO = {new['gate_fires']}")
        print(f"\n  FLIP: today reject={old['gate_fires']} -> fixed reject={new['gate_fires']}  ({'FLIPPED reject->feasible' if old['gate_fires'] and not new['gate_fires'] else 'no flip'})")

    # ---------- POPULATION + REGRESSION ----------
    print("\n" + "=" * 70)
    print("POPULATION + REGRESSION across ALL today's capture candidate-records")
    print("=" * 70)
    fixed_group = []   # was reject (today), now feasible: bag>=1 assigned, 0 picked up
    preserved = []     # still reject under fix (>=1 picked up) — rule correctly stays
    flip_other = []    # any other flip
    total_today_reject = 0
    for r in recs:
        old = compute_wait_gate(r, picked_up_counts_as_insertion=False)
        if not old["gate_fires"]:
            continue
        total_today_reject += 1
        new = compute_wait_gate(r, picked_up_counts_as_insertion=True)
        entry = {"oid": str(r.get("order_id")), "tier": r.get("tier"), "ts": r.get("ts"),
                 "n_picked": old["n_picked"], "n_total": old["n_total"], "max_wait": old["max_wait"],
                 "new_reject": new["gate_fires"], "wait_oid": old["wait_oid"]}
        if old["gate_fires"] and not new["gate_fires"]:
            # was reject, fix rescues it
            if old["n_picked"] == 0:
                fixed_group.append(entry)
            else:
                flip_other.append(entry)  # shouldn't happen: picked>=1 keeps reject
        else:
            preserved.append(entry)

    print(f"\nTotal candidate-records TODAY hit by wait-courier verdict-gate NO (today's len-bag rule): {total_today_reject}")
    print(f"  (a) NAPRAWIONE (was false-reject; bag>=1 assigned, 0 picked up -> fix rescues): {len(fixed_group)}")
    print(f"  (b) ZACHOWANE  (>=1 picked up -> rule correctly stays reject):                  {len(preserved)}")
    print(f"  (?) inne flipy (unexpected):                                                    {len(flip_other)}")

    def summarize(group, label, lim=12):
        from collections import Counter
        oids = Counter(e["oid"] for e in group)
        print(f"\n  {label}: {len(group)} candidate-records across {len(oids)} distinct new-orders")
        for e in group[:lim]:
            print(f"    oid={e['oid']} tier={e['tier']:5s} n_picked={e['n_picked']} n_total={e['n_total']} wait={e['max_wait']}min wait_oid={e['wait_oid']}")
        if len(group) > lim:
            print(f"    ... (+{len(group)-lim} more)")

    summarize(fixed_group, "NAPRAWIONE examples")
    summarize(preserved, "ZACHOWANE examples")
    if flip_other:
        summarize(flip_other, "UNEXPECTED flips")

    # ---------- COUNTEREXAMPLE: courier picked up food + long idle, must STAY rejected ----------
    print("\n" + "=" * 70)
    print("KONTRPRZYKŁAD: courier with >=1 PICKED-UP order + idle>15min — must STAY rejected under fix")
    print("=" * 70)
    if preserved:
        # sort by max_wait desc
        preserved.sort(key=lambda e: -e["max_wait"])
        print(f"Found {len(preserved)} such candidate-records. Top by idle:")
        for e in preserved[:6]:
            print(f"    oid={e['oid']} tier={e['tier']:5s} n_picked={e['n_picked']} n_total={e['n_total']} max_wait={e['max_wait']}min  fix_still_reject={e['new_reject']}")
    else:
        print("NONE today: no candidate-record had a picked-up bag order AND wait>15min triggering the gate.")
