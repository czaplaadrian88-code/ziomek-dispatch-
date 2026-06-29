#!/usr/bin/env python3
"""
N2 REGRESSION over 3 days (2026-06-14,-15,-16) — READ-ONLY.

Extends n2_replay.py to the wider window. Two independent analyses:

(A) FLIP / IDLE analysis from obj_replay_capture.jsonl (has picked_up_at per bag order):
    - per candidate-record re-derive the wait-courier verdict gate exactly as
      dispatch_pipeline.py computes it (OLD: bag_size=len(bag); NEW: bag_size=#picked_up).
    - count flips reject->feasible, split by n_picked==0 (INTENDED) vs n_picked>=1 (REGRESSION).
    - count preserved (still reject under fix).
    - idle-soft-penalty distribution on empty-handed candidates (compute_idle_wait_soft_penalty).
    - OSRM nondeterminism honesty: split flips into CERTAIN (max_wait>18) vs BORDERLINE (15-18).

OSRM cached in-process (lat,lng -> lat,lng pair -> minutes) to keep the 3-day run fast.
"""
import json, sys, math
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import audit_lib as AL
import urllib.request

CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
DAYS = ["2026-06-14", "2026-06-15", "2026-06-16"]
OSRM = "http://127.0.0.1:5001"

WAIT_THRESHOLD_MIN = 3.0
WAIT_HARD_REJECT_MIN = 15.0
DWELL_PICKUP_DEFAULT = 2.0
SKIP_FREE_COURIER = True
IDLE_SOFT_THR = 5.0
IDLE_SOFT_PER_MIN = -4.0

_OSRM_CACHE = {}
def osrm_min(a, b):
    key = (round(a[0], 6), round(a[1], 6), round(b[0], 6), round(b[1], 6))
    v = _OSRM_CACHE.get(key)
    if v is not None:
        return v
    url = f"{OSRM}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=false"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            d = json.load(resp)
        dur = d["routes"][0]["duration"] / 60.0
    except Exception:
        dur = None
    _OSRM_CACHE[key] = dur
    return dur

def parse(s):
    return AL.parse_ts(s)

def chain_arrivals(courier_pos, now_dt, pickups, dwell_pickup):
    arrivals = {}
    remaining = list(pickups)
    cur = tuple(courier_pos)
    t = now_dt
    while remaining:
        best_i, best_dur = None, None
        for i, p in enumerate(remaining):
            dur = osrm_min(cur, tuple(p["coords"]))
            if dur is None:
                dur = 1e9
            if best_dur is None or dur < best_dur:
                best_dur, best_i = dur, i
        p = remaining.pop(best_i)
        t = t + timedelta(minutes=best_dur)
        arrivals[p["oid"]] = t
        ready = p.get("ready_dt")
        dep_base = t if (ready is None or ready <= t) else ready
        t = dep_base + timedelta(minutes=dwell_pickup)
        cur = tuple(p["coords"])
    return arrivals

def compute_wait_gate(rec, picked_up_counts_as_insertion):
    bag = rec.get("bag") or []
    new_order = rec.get("new_order") or {}
    courier_pos = rec.get("courier_pos")
    now_dt = parse(rec.get("now"))
    dwell_pickup = AL.num(rec.get("dwell_pickup"), DWELL_PICKUP_DEFAULT) or DWELL_PICKUP_DEFAULT

    n_picked = sum(1 for b in bag if b.get("picked_up_at"))
    n_total = len(bag)
    bag_size = n_picked if picked_up_counts_as_insertion else n_total
    has_pending = any(b.get("picked_up_at") is None for b in bag)

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
                "n_picked": n_picked, "n_total": n_total, "has_pending": has_pending}

    arrivals = chain_arrivals(courier_pos, now_dt, pickups, dwell_pickup)
    max_wait = 0.0
    for p in pickups:
        arr = arrivals.get(p["oid"])
        ready = p.get("ready_dt")
        if arr is None or ready is None:
            continue
        w = max(0.0, (ready - arr).total_seconds() / 60.0)
        if w > max_wait:
            max_wait = w

    hard_reject = (bag_size >= 1 and max_wait > WAIT_THRESHOLD_MIN and max_wait > WAIT_HARD_REJECT_MIN)
    gate_fires = hard_reject and (has_pending or not SKIP_FREE_COURIER)
    return {"max_wait": round(max_wait, 2), "hard_reject": hard_reject, "gate_fires": gate_fires,
            "bag_size_used": bag_size, "n_picked": n_picked, "n_total": n_total, "has_pending": has_pending}

def idle_soft_pen(wait):
    if wait is None or wait <= IDLE_SOFT_THR:
        return 0.0
    return (wait - IDLE_SOFT_THR) * IDLE_SOFT_PER_MIN

def pctl(xs, q):
    if not xs:
        return 0.0
    s = sorted(xs)
    i = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[i]

def analyze_day(recs):
    fixed_intended = []   # was reject, now feasible, n_picked==0
    fixed_regress = []    # was reject, now feasible, n_picked>=1  (REGRESSION)
    preserved = []        # still reject under fix
    total_old_reject = 0
    # idle: on empty-handed candidates (n_picked==0) with bag>=1 assigned (the new soft regime),
    # AND also for truly bag-empty couriers? No: soft idle only applies when picked_up_count==0
    # AND bag_size(len)>=1? Actually production applies idle when picked==0 and max_wait>0,
    # regardless of assigned count (it's the empty-handed branch). We mirror: picked==0.
    idle_minutes = []
    idle_penalties = []
    for r in recs:
        old = compute_wait_gate(r, picked_up_counts_as_insertion=False)
        new = compute_wait_gate(r, picked_up_counts_as_insertion=True)
        # idle soft penalty applies to empty-handed (picked==0) candidates with positive wait
        if new["n_picked"] == 0 and new["max_wait"] > 0:
            idle_minutes.append(new["max_wait"])
            idle_penalties.append(idle_soft_pen(new["max_wait"]))
        if not old["gate_fires"]:
            continue
        total_old_reject += 1
        entry = {"oid": str(r.get("order_id")), "tier": r.get("tier"),
                 "n_picked": old["n_picked"], "n_total": old["n_total"], "max_wait": old["max_wait"]}
        if not new["gate_fires"]:
            if old["n_picked"] == 0:
                fixed_intended.append(entry)
            else:
                fixed_regress.append(entry)
        else:
            preserved.append(entry)
    return {
        "total_old_reject": total_old_reject,
        "fixed_intended": fixed_intended,
        "fixed_regress": fixed_regress,
        "preserved": preserved,
        "idle_minutes": idle_minutes,
        "idle_penalties": idle_penalties,
    }

def load_capture():
    by_day = {d: [] for d in DAYS}
    with open(CAPTURE) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            d = AL.wday(r.get("ts"))
            if d in by_day:
                by_day[d].append(r)
    return by_day

if __name__ == "__main__":
    print("loading capture (14-16.06) ...", flush=True)
    by_day = load_capture()
    grand = {"fixed_intended": [], "fixed_regress": [], "preserved": [], "idle_minutes": [], "idle_penalties": [], "total_old_reject": 0}
    per_day_res = {}
    for d in DAYS:
        recs = by_day[d]
        print(f"\n=== {d}: {len(recs)} candidate-records ===", flush=True)
        res = analyze_day(recs)
        per_day_res[d] = res
        fi, fr, pr = res["fixed_intended"], res["fixed_regress"], res["preserved"]
        # certain vs borderline split for flips
        def split_cb(group):
            certain = [e for e in group if e["max_wait"] > 18.0]
            border = [e for e in group if 15.0 < e["max_wait"] <= 18.0]
            return len(certain), len(border)
        fi_c, fi_b = split_cb(fi)
        fr_c, fr_b = split_cb(fr)
        im = res["idle_minutes"]; ip = res["idle_penalties"]
        ip_nonzero = [p for p in ip if p < 0]
        print(f"  OLD verdict-gate rejects (len-bag rule):        {res['total_old_reject']}")
        print(f"  (a) FLIP intended  (n_picked==0 -> feasible):   {len(fi)}   [certain>18min: {fi_c} | borderline 15-18: {fi_b}]")
        print(f"  (b) FLIP REGRESSION(n_picked>=1 -> feasible):   {len(fr)}   [certain>18min: {fr_c} | borderline 15-18: {fr_b}]")
        print(f"  (c) PRESERVED      (n_picked>=1 still reject):  {len(pr)}")
        # distinct orders
        from collections import Counter
        print(f"      distinct new-orders: intended={len(set(e['oid'] for e in fi))} regress={len(set(e['oid'] for e in fr))} preserved={len(set(e['oid'] for e in pr))}")
        print(f"  IDLE soft-penalty (empty-handed cands, picked==0, wait>0): n={len(im)}, of which penalized(>5min)={len(ip_nonzero)}")
        if im:
            print(f"      idle minutes: median={pctl(im,0.5):.1f}  p90={pctl(im,0.9):.1f}  max={max(im):.1f}")
        if ip_nonzero:
            print(f"      idle penalty: median={pctl(ip_nonzero,0.5):.1f}  p90={pctl(ip_nonzero,0.1):.1f}  max(most-neg)={min(ip_nonzero):.1f}")
            absurd = [p for p in ip_nonzero if p < -300]
            print(f"      ABSURD penalties (< -300): {len(absurd)}" + (f"  e.g. {sorted(absurd)[:3]}" if absurd else ""))
        if fr:
            print(f"  !!! REGRESSION FLIPS (n_picked>=1 reject->feasible) — should be ZERO:")
            for e in fr[:20]:
                print(f"        oid={e['oid']} tier={e['tier']} n_picked={e['n_picked']} n_total={e['n_total']} max_wait={e['max_wait']}")
        for k in ("fixed_intended", "fixed_regress", "preserved", "idle_minutes", "idle_penalties"):
            grand[k].extend(res[k])
        grand["total_old_reject"] += res["total_old_reject"]

    print("\n" + "=" * 70)
    print("RAZEM 14-16.06")
    print("=" * 70)
    fi, fr, pr = grand["fixed_intended"], grand["fixed_regress"], grand["preserved"]
    im = grand["idle_minutes"]; ip = [p for p in grand["idle_penalties"] if p < 0]
    print(f"  OLD verdict-gate rejects total:               {grand['total_old_reject']}")
    print(f"  FLIP intended (picked==0):                    {len(fi)}")
    print(f"  FLIP REGRESSION (picked>=1):                  {len(fr)}")
    print(f"  PRESERVED:                                    {len(pr)}")
    print(f"  IDLE penalized candidates total:              {len(ip)}")
    if im:
        print(f"  idle minutes overall: median={pctl(im,0.5):.1f} p90={pctl(im,0.9):.1f} max={max(im):.1f}")
    if ip:
        print(f"  idle penalty overall: median={pctl(ip,0.5):.1f} max(most-neg)={min(ip):.1f}  absurd<-300={len([p for p in ip if p<-300])}")
    # write json for cross-use
    out = {d: {"total_old_reject": per_day_res[d]["total_old_reject"],
               "fixed_intended": len(per_day_res[d]["fixed_intended"]),
               "fixed_regress": len(per_day_res[d]["fixed_regress"]),
               "preserved": len(per_day_res[d]["preserved"]),
               "regress_detail": per_day_res[d]["fixed_regress"]} for d in DAYS}
    with open("/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17/n2_3day_flips.json", "w") as g:
        json.dump(out, g, ensure_ascii=False, indent=2)
    print("\n[written n2_3day_flips.json]")
