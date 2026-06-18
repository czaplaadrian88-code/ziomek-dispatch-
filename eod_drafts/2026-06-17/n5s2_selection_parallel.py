#!/usr/bin/env python3
"""
N5-S2 SELECTION-IMPACT PROXY — PARALLEL driver (READ-ONLY). 2026-06-18.

Same question/logic as n5s2_selection.py, faster (4 workers, one decision per
work-unit). Reuses the reviewed metric helpers from n5s2_committed_penalty_replay
(mk, committed_late_max, ewma_for, build_ewma_index, has_ck, pts, wday, patch_*).

A "decision" = all capture rows sharing (order_id, now) = the full candidate pool.
We re-simulate EVERY candidate OFF vs ON and ask: does the chosen courier flip?

CAVEAT (honesty, verbatim from serial): production selection uses the full
scoring.py stack (tiers, wait penalties, R6 gates, demotes). We do NOT have
per-candidate scores in the capture. So this is a ROUTE-QUALITY PROXY: rank
candidates by a transparent key that selection broadly minimizes; N5-S2 only
changes plan timing, never feasibility/courier identity.
  RANK-A  total_duration_min ASC  (pure route length — what OR-Tools cost tracks)
  RANK-B  (committed_late_max>tol ? committed_late_max : 0, total_duration) ASC
We report, per decision: does argmin flip OFF->ON, and if so is the ON-picked
candidate's committed_late_max WORSE (regression) / better / equal vs OFF-picked.
"""
import json, sys, argparse
from collections import defaultdict
from multiprocessing import Pool
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")

import dispatch_v2.common as C
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, set_committed_pickup_tolerance
import n5s2_committed_penalty_replay as M

CAP = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
DAYS = M.DAYS
WINDOW_MAX_MIN = M.WINDOW_MAX_MIN
EWMA_THRESHOLD = M.EWMA_THRESHOLD
TOL_STRICT = M.TOL_STRICT
TOL_LOOSE = M.TOL_LOOSE

_BY_OID = None


def _init(by_oid, coeff):
    global _BY_OID
    _BY_OID = by_oid
    C.OBJ_COMMITTED_PICKUP_PENALTY_COEFF = float(coeff)


def _process(task):
    (oid, nowiso, rows) = task
    day = M.wday(nowiso)
    now = M.pts(nowiso)
    out = dict(day=day, decisions=1, multi=0, flipA=0, flipB=0,
               flipA_worse=0, flipA_better=0, flipA_equal=0,
               flipB_worse=0, flipB_better=0, flipB_equal=0, example=None)
    no = rows[0].get("new_order") or {}
    ready = M.pts(no.get("pickup_ready_at"))
    if now and ready and (ready - now).total_seconds() / 60.0 > WINDOW_MAX_MIN:
        out["decisions"] = 0  # far-czasówka: not a real proposal, drop entirely
        return out
    if len(rows) < 2:
        return out
    out["multi"] = 1

    ewma = M.ewma_for(_BY_OID, oid, now)
    tol = TOL_LOOSE if (ewma is not None and ewma >= EWMA_THRESHOLD) else TOL_STRICT

    cand_off, cand_on = [], []
    for idx, d in enumerate(rows):
        cp = tuple(d["courier_pos"]) if d.get("courier_pos") else None
        if cp is None:
            continue
        bag = d.get("bag") or []
        ck_by_oid = {str(o.get("order_id")): M.pts(o.get("czas_kuriera_warsaw"))
                     for o in bag if M.has_ck(o) and M.pts(o.get("czas_kuriera_warsaw"))}

        M.patch_off()
        set_committed_pickup_tolerance(None)
        try:
            poff = simulate_bag_route_v2(cp, [M.mk(o) for o in bag], M.mk(no), now=now)
        except Exception:
            continue
        M.patch_on()
        set_committed_pickup_tolerance(tol)
        try:
            pon = simulate_bag_route_v2(cp, [M.mk(o) for o in bag], M.mk(no), now=now)
        except Exception:
            M.patch_off()
            continue
        M.patch_off()
        set_committed_pickup_tolerance(None)

        clo = M.committed_late_max(poff, ck_by_oid)[0] if isinstance(
            M.committed_late_max(poff, ck_by_oid), tuple) else M.committed_late_max(poff, ck_by_oid)
        cln = M.committed_late_max(pon, ck_by_oid)[0] if isinstance(
            M.committed_late_max(pon, ck_by_oid), tuple) else M.committed_late_max(pon, ck_by_oid)
        cand_off.append(dict(idx=idx, dur=poff.total_duration_min,
                             clate=(clo if clo is not None else -1e9)))
        cand_on.append(dict(idx=idx, dur=pon.total_duration_min,
                            clate=(cln if cln is not None else -1e9)))
    if len(cand_off) < 2:
        return out

    bestA_off = min(cand_off, key=lambda c: c["dur"])
    bestA_on = min(cand_on, key=lambda c: c["dur"])

    def keyB(c):
        breach = c["clate"] if c["clate"] > tol else 0.0
        return (breach, c["dur"])
    bestB_off = min(cand_off, key=keyB)
    bestB_on = min(cand_on, key=keyB)

    if bestA_off["idx"] != bestA_on["idx"]:
        out["flipA"] = 1
        on_c = next(c["clate"] for c in cand_on if c["idx"] == bestA_on["idx"])
        off_c = next(c["clate"] for c in cand_off if c["idx"] == bestA_off["idx"])
        if on_c > off_c + 0.5:
            out["flipA_worse"] = 1
        elif on_c < off_c - 0.5:
            out["flipA_better"] = 1
        else:
            out["flipA_equal"] = 1

    if bestB_off["idx"] != bestB_on["idx"]:
        out["flipB"] = 1
        on_c = next(c["clate"] for c in cand_on if c["idx"] == bestB_on["idx"])
        off_c = next(c["clate"] for c in cand_off if c["idx"] == bestB_off["idx"])
        verdict = "worse" if on_c > off_c + 0.5 else ("better" if on_c < off_c - 0.5 else "equal")
        out["flipB_" + verdict] = 1
        out["example"] = dict(oid=oid, day=day, off_clate=round(off_c, 1),
                              on_clate=round(on_c, 1), verdict=verdict, tol=tol)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coeff", type=int, default=100)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    groups = defaultdict(list)
    nwin = 0
    with open(CAP) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if M.wday(d.get("now")) not in DAYS:
                continue
            nwin += 1
            groups[(str(d.get("order_id")), d.get("now"))].append(d)
    tasks = [(oid, nowiso, rows) for (oid, nowiso), rows in groups.items()]

    by_oid = M.build_ewma_index()
    print("=" * 96)
    print(f"N5-S2 SELECTION-IMPACT PROXY (PARALLEL, coeff={args.coeff}) — does best-pick flip OFF->ON?")
    print("RANK-A = shortest total_duration | RANK-B = lexical(committed-breach beyond tol, then duration)")
    print("PROXY ONLY: real selection uses full scoring.py; penalty changes plan timing, not pool")
    print("=" * 96)
    print(f"in-window rows: {nwin} | decisions (groups): {len(tasks)} | workers: {args.workers}\n", flush=True)

    with Pool(args.workers, initializer=_init, initargs=(by_oid, args.coeff)) as pool:
        results = pool.map(_process, tasks, chunksize=16)

    per_day = defaultdict(lambda: defaultdict(int))
    examples = []
    for r in results:
        P = per_day[r["day"]]
        for k in ("decisions", "multi", "flipA", "flipA_worse", "flipA_better", "flipA_equal",
                  "flipB", "flipB_worse", "flipB_better", "flipB_equal"):
            P[k] += r[k]
        if r["example"]:
            examples.append(r["example"])

    print(f"{'day':<11}{'decisions':>10}{'multi-cand':>11}"
          f"{'flipA':>7}{'A:worse/better/eq':>20}{'flipB':>7}{'B:worse/better/eq':>20}")
    T = defaultdict(int)
    for day in DAYS:
        P = per_day[day]
        print(f"{day:<11}{P['decisions']:>10}{P['multi']:>11}"
              f"{P['flipA']:>7}{(str(P['flipA_worse'])+'/'+str(P['flipA_better'])+'/'+str(P['flipA_equal'])):>20}"
              f"{P['flipB']:>7}{(str(P['flipB_worse'])+'/'+str(P['flipB_better'])+'/'+str(P['flipB_equal'])):>20}")
        for k in P:
            T[k] += P[k]
    print("-" * 96)
    print(f"{'TOTAL':<11}{T['decisions']:>10}{T['multi']:>11}"
          f"{T['flipA']:>7}{(str(T['flipA_worse'])+'/'+str(T['flipA_better'])+'/'+str(T['flipA_equal'])):>20}"
          f"{T['flipB']:>7}{(str(T['flipB_worse'])+'/'+str(T['flipB_better'])+'/'+str(T['flipB_equal'])):>20}")
    print(f"\nmulti-candidate decisions: {T['multi']}")
    print(f"RANK-A best-pick flips:    {T['flipA']} ({100.0*T['flipA']/max(1,T['multi']):.1f}%) — "
          f"worse {T['flipA_worse']} / better {T['flipA_better']} / equal {T['flipA_equal']}")
    print(f"RANK-B best-pick flips:    {T['flipB']} ({100.0*T['flipB']/max(1,T['multi']):.1f}%) — "
          f"worse {T['flipB_worse']} / better {T['flipB_better']} / equal {T['flipB_equal']}")
    if examples:
        print("\n--- RANK-B flip examples (committed-late of picked candidate OFF vs ON) ---")
        for e in examples[:15]:
            print(f"   {e['day']} oid={e['oid']} OFF_pick_clate={e['off_clate']} "
                  f"ON_pick_clate={e['on_clate']} -> {e['verdict']} (tol={e['tol']})")


if __name__ == "__main__":
    main()
