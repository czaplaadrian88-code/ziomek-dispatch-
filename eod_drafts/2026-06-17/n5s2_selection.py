#!/usr/bin/env python3
"""
N5-S2 SELECTION-IMPACT PROXY — does the committed penalty change the best-pick?
2026-06-17 READ-ONLY. Companion to n5s2_committed_penalty_replay.py.

The capture writes one row per CANDIDATE per decision; rows sharing the same
(order_id, now) are the full candidate pool for that decision. We re-simulate
EVERY candidate OFF vs ON and look at whether the chosen courier flips.

CAVEAT (honesty): production selection uses the full scoring.py stack (tiers,
wait penalties, R6 gates, demotes). We do NOT have per-candidate scores in the
capture. So this is a ROUTE-QUALITY PROXY: we rank candidates by a transparent
key that selection broadly minimizes, and N5-S2 only changes plan timing, never
feasibility/courier identity. Two proxy rankers reported:
  RANK-A  total_duration_min ASC  (pure route length — what OR-Tools cost tracks)
  RANK-B  (committed_late_max>tol ? committed_late_max : 0, total_duration) ASC
          (lexical: avoid committed breach first, then shortest — mirrors intent)
We report, per decision: does argmin flip OFF->ON, and if so is the ON-picked
candidate's committed_late_max WORSE than the OFF-picked one's (regression) or
better/equal. Far-czasówka filter applied on the decision's new_order.
"""
import json, sys, argparse
from datetime import datetime, timedelta, timezone
from collections import defaultdict
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import dispatch_v2.common as C
from dispatch_v2.route_simulator_v2 import (
    simulate_bag_route_v2, OrderSim, set_committed_pickup_tolerance)

CAP = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
SHADOW = ["/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
          "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1"]
DAYS = ("2026-06-14", "2026-06-15", "2026-06-16")
WINDOW_MAX_MIN = 65.0
WARSAW_OFF = 2
EWMA_THRESHOLD = 4.5
TOL_STRICT = 5.0
TOL_LOOSE = 10.0


def pts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def wday(ts):
    d = pts(ts)
    return (d + timedelta(hours=WARSAW_OFF)).strftime("%Y-%m-%d") if d else None


def has_ck(o):
    v = o.get("czas_kuriera_warsaw")
    return v is not None and str(v).strip() not in ("", "None", "null", "NULL")


def mk(o):
    sim = OrderSim(
        order_id=str(o.get("order_id")),
        pickup_coords=tuple(o["pickup_coords"]),
        delivery_coords=tuple(o["delivery_coords"]) if o.get("delivery_coords") else tuple(o["pickup_coords"]),
        picked_up_at=pts(o.get("picked_up_at")),
        status=o.get("status") or "assigned",
        pickup_ready_at=pts(o.get("pickup_ready_at")),
    )
    if has_ck(o):
        sim.czas_kuriera_warsaw = o.get("czas_kuriera_warsaw")
    return sim


def committed_late_max(plan, ck_by_oid):
    if plan is None or not getattr(plan, "pickup_at", None):
        return None
    worst = None
    for oid, ck in ck_by_oid.items():
        pat = plan.pickup_at.get(oid)
        if pat is None:
            continue
        late = (pat - ck).total_seconds() / 60.0
        if worst is None or late > worst:
            worst = late
    return worst


_ORIG_FLAG = C.decision_flag


def patch_on():
    C.decision_flag = (lambda name: True if name == "ENABLE_OBJ_COMMITTED_PICKUP_PENALTY"
                       else _ORIG_FLAG(name))


def patch_off():
    C.decision_flag = _ORIG_FLAG


def build_ewma_index():
    by_oid = defaultdict(list)
    for fn in SHADOW:
        try:
            fh = open(fn)
        except Exception:
            continue
        for line in fh:
            if "loadgov_load_ewma" not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if wday(d.get("ts")) not in DAYS:
                continue
            ewma = (d.get("best") or {}).get("loadgov_load_ewma")
            if ewma is None:
                continue
            t = pts(d.get("ts"))
            if t is None:
                continue
            by_oid[str(d.get("order_id"))].append((t, ewma))
        fh.close()
    return by_oid


def ewma_for(by_oid, oid, cnow):
    cands = by_oid.get(str(oid))
    if not cands or cnow is None:
        return None
    best = min(cands, key=lambda x: abs((x[0] - cnow).total_seconds()))
    if abs((best[0] - cnow).total_seconds()) < 5.0:
        return best[1]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coeff", type=int, default=100)
    args = ap.parse_args()
    C.OBJ_COMMITTED_PICKUP_PENALTY_COEFF = float(args.coeff)

    # group capture rows by (order_id, now) = decision; each row is a candidate
    groups = defaultdict(list)
    with open(CAP) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if wday(d.get("now")) not in DAYS:
                continue
            groups[(str(d.get("order_id")), d.get("now"))].append(d)

    by_oid = build_ewma_index()

    per_day = defaultdict(lambda: dict(
        decisions=0, multi=0, flipA=0, flipB=0,
        flipA_worse=0, flipA_better=0, flipA_equal=0,
        flipB_worse=0, flipB_better=0, flipB_equal=0,
        flip_examples=[]))

    for (oid, nowiso), rows in groups.items():
        day = wday(nowiso)
        now = pts(nowiso)
        # far-czasówka filter on the decision's new_order (same for all rows)
        no = rows[0].get("new_order") or {}
        ready = pts(no.get("pickup_ready_at"))
        if now and ready and (ready - now).total_seconds() / 60.0 > WINDOW_MAX_MIN:
            continue
        P = per_day[day]
        P["decisions"] += 1
        if len(rows) < 2:
            continue
        P["multi"] += 1

        ewma = ewma_for(by_oid, oid, now)
        tol = TOL_LOOSE if (ewma is not None and ewma >= EWMA_THRESHOLD) else TOL_STRICT

        cand_off = []
        cand_on = []
        for idx, d in enumerate(rows):
            cp = tuple(d["courier_pos"]) if d.get("courier_pos") else None
            if cp is None:
                continue
            bag = d.get("bag") or []
            ck_by_oid = {str(o.get("order_id")): pts(o.get("czas_kuriera_warsaw"))
                         for o in bag if has_ck(o) and pts(o.get("czas_kuriera_warsaw"))}
            nw = mk(no)

            patch_off()
            set_committed_pickup_tolerance(None)
            try:
                poff = simulate_bag_route_v2(cp, [mk(o) for o in bag], nw, now=now)
            except Exception:
                continue
            patch_on()
            set_committed_pickup_tolerance(tol)
            try:
                pon = simulate_bag_route_v2(cp, [mk(o) for o in bag], mk(no), now=now)
            except Exception:
                patch_off()
                continue
            patch_off()
            set_committed_pickup_tolerance(None)

            clo = committed_late_max(poff, ck_by_oid)
            cln = committed_late_max(pon, ck_by_oid)
            cand_off.append(dict(idx=idx, dur=poff.total_duration_min,
                                 clate=(clo if clo is not None else -1e9),
                                 tier=d.get("tier")))
            cand_on.append(dict(idx=idx, dur=pon.total_duration_min,
                                clate=(cln if cln is not None else -1e9),
                                tier=d.get("tier")))
        if len(cand_off) < 2:
            continue

        # RANK-A: shortest total_duration
        bestA_off = min(cand_off, key=lambda c: c["dur"])
        bestA_on = min(cand_on, key=lambda c: c["dur"])
        # RANK-B: lexical (committed breach beyond tol first, then duration)
        def keyB(c):
            breach = c["clate"] if c["clate"] > tol else 0.0
            return (breach, c["dur"])
        bestB_off = min(cand_off, key=keyB)
        bestB_on = min(cand_on, key=keyB)

        if bestA_off["idx"] != bestA_on["idx"]:
            P["flipA"] += 1
            # compare committed-late of the ON-picked vs OFF-picked candidate
            on_pick_clate = next(c["clate"] for c in cand_on if c["idx"] == bestA_on["idx"])
            off_pick_clate = next(c["clate"] for c in cand_off if c["idx"] == bestA_off["idx"])
            if on_pick_clate > off_pick_clate + 0.5:
                P["flipA_worse"] += 1
            elif on_pick_clate < off_pick_clate - 0.5:
                P["flipA_better"] += 1
            else:
                P["flipA_equal"] += 1

        if bestB_off["idx"] != bestB_on["idx"]:
            P["flipB"] += 1
            on_pick_clate = next(c["clate"] for c in cand_on if c["idx"] == bestB_on["idx"])
            off_pick_clate = next(c["clate"] for c in cand_off if c["idx"] == bestB_off["idx"])
            verdict = "worse" if on_pick_clate > off_pick_clate + 0.5 else (
                "better" if on_pick_clate < off_pick_clate - 0.5 else "equal")
            if verdict == "worse":
                P["flipB_worse"] += 1
            elif verdict == "better":
                P["flipB_better"] += 1
            else:
                P["flipB_equal"] += 1
            if len(P["flip_examples"]) < 8:
                P["flip_examples"].append(dict(
                    oid=oid, day=day, off_clate=round(off_pick_clate, 1),
                    on_clate=round(on_pick_clate, 1), verdict=verdict, tol=tol))

    print("=" * 96)
    print(f"N5-S2 SELECTION-IMPACT PROXY (coeff={args.coeff}) — does best-pick flip OFF->ON?")
    print("RANK-A = shortest total_duration | RANK-B = lexical(committed-breach, then duration)")
    print("PROXY ONLY: real selection uses full scoring.py; penalty changes plan timing, not pool")
    print("=" * 96)
    print(f"{'day':<11}{'decisions':>10}{'multi-cand':>11}"
          f"{'flipA':>7}{'A:worse/better/eq':>20}"
          f"{'flipB':>7}{'B:worse/better/eq':>20}")
    T = defaultdict(int)
    allex = []
    for day in DAYS:
        P = per_day[day]
        print(f"{day:<11}{P['decisions']:>10}{P['multi']:>11}"
              f"{P['flipA']:>7}{(str(P['flipA_worse'])+'/'+str(P['flipA_better'])+'/'+str(P['flipA_equal'])):>20}"
              f"{P['flipB']:>7}{(str(P['flipB_worse'])+'/'+str(P['flipB_better'])+'/'+str(P['flipB_equal'])):>20}")
        for k in ("decisions", "multi", "flipA", "flipA_worse", "flipA_better", "flipA_equal",
                  "flipB", "flipB_worse", "flipB_better", "flipB_equal"):
            T[k] += P[k]
        allex += P["flip_examples"]
    print("-" * 96)
    print(f"{'TOTAL':<11}{T['decisions']:>10}{T['multi']:>11}"
          f"{T['flipA']:>7}{(str(T['flipA_worse'])+'/'+str(T['flipA_better'])+'/'+str(T['flipA_equal'])):>20}"
          f"{T['flipB']:>7}{(str(T['flipB_worse'])+'/'+str(T['flipB_better'])+'/'+str(T['flipB_equal'])):>20}")
    print(f"\nmulti-candidate decisions: {T['multi']}")
    print(f"RANK-A best-pick flips:    {T['flipA']} ({100.0*T['flipA']/max(1,T['multi']):.1f}%) — "
          f"worse {T['flipA_worse']} / better {T['flipA_better']} / equal {T['flipA_equal']}")
    print(f"RANK-B best-pick flips:    {T['flipB']} ({100.0*T['flipB']/max(1,T['multi']):.1f}%) — "
          f"worse {T['flipB_worse']} / better {T['flipB_better']} / equal {T['flipB_equal']}")
    if allex:
        print("\n--- RANK-B flip examples (committed-late of picked candidate OFF vs ON) ---")
        for e in allex[:12]:
            print(f"   {e['day']} oid={e['oid']} OFF_pick_clate={e['off_clate']} "
                  f"ON_pick_clate={e['on_clate']} -> {e['verdict']} (tol={e['tol']})")


if __name__ == "__main__":
    main()
