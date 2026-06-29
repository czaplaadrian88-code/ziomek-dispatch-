#!/usr/bin/env python3
"""Per-case investigation dump for the Ziomek hard-cases audit. READ-ONLY.
Usage: python casetool.py <order_id> [<order_id> ...]
Prints, for each order's dominated decision(s): chosen best vs dominator(s) on the TRUE objective,
hidden-disqualifier fields (coordinator/ramp/veto/hard-reject), score-axis breakdown, capture
geometry + independent OSRM recompute of the chosen path, and the real outcome."""
import json, sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import audit_lib as L

FULL = json.load(open(f"{L.SCRATCH}/deepdive_full_records.json"))
CASES = json.load(open(f"{L.SCRATCH}/dominated_cases.json"))
CASES_BY = {}
for c in CASES: CASES_BY.setdefault(c["order_id"], []).append(c)

# hidden-disqualifier fields to surface per candidate
DISQ = ["feasibility","reason","best_effort","is_coordinator","coordinator_active","bonus_coordinator_idle",
        "new_courier_ramp","v325_new_courier_flag","v325_new_courier_penalty","v326_wave_veto",
        "fifo_violations","carry_chain_hard_reject","carry_chain_applied","intra_rest_gap_hard_reject",
        "v3273_wait_courier_hard_reject","r6_picked_up_delta_reject","paczka_is","paczka_flex_eligible",
        "shift_end_edge","pre_shift_clamp_applied","soon_free_applied","soon_free_free_at_min"]
# score-axis terms to surface (why Ziomek scored best above dominator)
SCOREAX = ["score","bonus_penalty_sum","bonus_r6_soft_pen","bonus_r1_soft_pen","bonus_r5_soft_pen",
           "bonus_r8_soft_pen","bonus_r9_stopover","bonus_r9_wait_pen","bonus_v3273_wait_courier",
           "bonus_coordinator_idle","bundle_bonus","bonus_l1","bonus_l2","timing_gap_bonus",
           "bonus_r1_corridor","bonus_r5_detour","carry_chain_penalty","v325_new_courier_advantage",
           "v326_speed_score_adjustment","v326_fleet_load_adjustment","a2_reliability_delta",
           "bonus_gps_age_discount","new_courier_advantage"]
OBJ = ["courier_id","name","pos_source","feasibility","km_to_pickup","drive_min","travel_min",
       "time_to_pickup_ready_min","free_at_min","r6_max_bag_time_min","r6_worst_oid","r6_bag_size",
       "objm_r6_breach_max_min","objm_r6_breach_count","late_pickup_committed_breach",
       "late_pickup_committed_max","late_pickup_committed_worst_oid","new_pickup_late_min",
       "new_pickup_needs_extension","eta_pickup_hhmm"]

def find_cand(rec, cid):
    cid=str(cid)
    if str((rec.get("best") or {}).get("courier_id"))==cid: return rec.get("best")
    for a in rec.get("alternatives") or []:
        if str(a.get("courier_id"))==cid: return a
    return None

def show(c, tag):
    if not c: print(f"  {tag}: <not found>"); return
    print(f"  {tag}: cid={c.get('courier_id')} {c.get('name')}")
    print(f"     OBJ : " + " ".join(f"{k}={c.get(k)}" for k in OBJ if k in c and k not in ('courier_id','name')))
    dq = {k:c.get(k) for k in DISQ if k in c and c.get(k) not in (None,False,0,0.0)}
    print(f"     DISQ: {json.dumps(dq, ensure_ascii=False)}")
    sx = {k:c.get(k) for k in SCOREAX if k in c and c.get(k) not in (None,0,0.0)}
    print(f"     SCORE: {json.dumps(sx, ensure_ascii=False)}")

def main(oids):
    cap = L.load_capture_for(oids)
    bf = {str(r['order_id']):r for r in L.load_backfill_window()}
    for oid in oids:
        oid=str(oid)
        print("\n" + "="*100)
        print(f"ORDER {oid}")
        recs = FULL.get(oid) or []
        cs = CASES_BY.get(oid) or []
        if not recs: print("  no full shadow record"); continue
        for ci, case in enumerate(cs):
            print(f"\n--- dominated decision ts={case['ts']} bucket={case['bucket']} E2={case['e2_sig']} verdict={case['verdict']} ---")
            print(f"    restaurant={case['restaurant']} pool_total={case['pool_total']} pool_feas={case['pool_feas']} auto_route_reason={case['auto_route_reason']}")
            # find matching full record by ts
            rec = next((r for r in recs if r.get("ts")==case["ts"]), recs[0])
            best = rec.get("best") or {}
            show(best, "CHOSEN BEST")
            for d in case["all_dominators"]:
                dc = find_cand(rec, d["cid"])
                de = d["deltas"]
                print(f"  >>> DOMINATOR cid={d['cid']} deltas: dR6={de['d_r6_max_bag']} dCommit={de['d_committed_max']} dNewLate={de['d_new_pickup_late']} dBreachCnt={de['d_breach_count']} (relA={de['A_rel']}<=relB={de['B_rel']}, feasA={de['A_feas']}<=feasB={de['B_feas']})")
                show(dc, "  dominator")
        # capture geometry + OSRM recompute for chosen
        crows = cap.get(oid) or []
        if crows:
            cr = crows[-1]
            no = cr.get("new_order") or {}
            cpos = cr.get("courier_pos"); pk = no.get("pickup_coords"); dl = no.get("delivery_coords")
            print(f"\n  CAPTURE(chosen geom): courier_pos={cpos} bag={len(cr.get('bag') or [])} pickup={pk} deliv={dl} ready={no.get('pickup_ready_at')}")
            if cpos and pk and dl:
                d1=L.osrm_route(tuple(cpos), tuple(pk)); d2=L.osrm_route(tuple(pk), tuple(dl))
                print(f"  OSRM indep: courier->pickup={d1[0]:.1f}min/{d1[1]:.2f}km  pickup->deliv={d2[0]:.1f}min/{d2[1]:.2f}km  (vs ziomek best.drive_min={best.get('drive_min')})")
        # outcome anchor
        o = (bf.get(oid) or {}).get("outcome") or {}
        print(f"  OUTCOME: status={o.get('status')} courier_final={o.get('courier_id_final')} assign_to_pickup={o.get('assign_to_pickup_min')} pickup_to_deliv={o.get('pickup_to_delivery_min')} assign_to_deliv={o.get('assign_to_delivery_min')}")

if __name__ == "__main__":
    main(sys.argv[1:] or ["481340"])
