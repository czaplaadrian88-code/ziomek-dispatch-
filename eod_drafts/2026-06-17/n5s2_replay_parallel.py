#!/usr/bin/env python3
"""
N5-S2 VALIDATION REPLAY — PARALLEL driver (READ-ONLY). 2026-06-18.

Same verdict as n5s2_committed_penalty_replay.py, faster:
  - reuses that script's metric functions verbatim (mk, committed_late_max,
    is_fallback, ewma_for, build_ewma_index, load_records, patch_on/off, pts...)
  - computes the OFF plan ONCE per record (OFF does not depend on coeff; the
    serial script recomputed it per coeff = 3x waste)
  - shards records across N worker processes (box has 4 vCPU)

OFF and ON run sequentially inside one worker for a given record, so the
OFF-vs-ON delta stays apples-to-apples even under CPU contention (both sides
get the same ~200ms OR-Tools wall budget back-to-back).

Output: same per-coeff tables + summary + regression dump as the serial script.
"""
import json, sys, argparse
from collections import defaultdict
from multiprocessing import Pool
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")

import dispatch_v2.common as C
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, set_committed_pickup_tolerance
import n5s2_committed_penalty_replay as M  # reuse reviewed metric logic

BORDERLINE_MIN = M.BORDERLINE_MIN
EWMA_THRESHOLD = M.EWMA_THRESHOLD
TOL_STRICT = M.TOL_STRICT
TOL_LOOSE = M.TOL_LOOSE
DAYS = M.DAYS

# globals set in each worker via initializer
_BY_OID = None
_COEFFS = None


def _init(by_oid, coeffs):
    global _BY_OID, _COEFFS
    _BY_OID = by_oid
    _COEFFS = coeffs


def _process(rec):
    """One record -> dict(day=..., off_nolate/simfail bool, per-coeff result list).
    per-coeff entry: (coeff, kind, value, regr_detail|None)
      kind in {'red','regr','border','infeasible_on_only'}; value=abs(delta).
    Also reports infeasible_off (once)."""
    (day, d, now, no, bag) = rec
    out = dict(day=day, simfail=False, off_nolate=False,
               infeasible_off=False, coeffs=[])
    cp = tuple(d["courier_pos"]) if d.get("courier_pos") else None
    if cp is None:
        out["skip"] = True
        return out
    ck_by_oid = {}
    for o in bag:
        if M.has_ck(o):
            ck = M.pts(o.get("czas_kuriera_warsaw"))
            if ck:
                ck_by_oid[str(o.get("order_id"))] = ck
    if not ck_by_oid:
        out["skip"] = True
        return out

    ewma = M.ewma_for(_BY_OID, d.get("order_id"), now)
    tol = TOL_LOOSE if (ewma is not None and ewma >= EWMA_THRESHOLD) else TOL_STRICT

    # ---- OFF once ----
    M.patch_off()
    set_committed_pickup_tolerance(None)
    try:
        plan_off = simulate_bag_route_v2(cp, [M.mk(o) for o in bag], M.mk(no), now=now)
    except Exception:
        out["simfail"] = True
        return out
    out["infeasible_off"] = M.is_fallback(plan_off)
    lo, _ = M.committed_late_max(plan_off, ck_by_oid)

    # ---- ON per coeff ----
    for coeff in _COEFFS:
        C.OBJ_COMMITTED_PICKUP_PENALTY_COEFF = float(coeff)
        M.patch_on()
        set_committed_pickup_tolerance(tol)
        try:
            plan_on = simulate_bag_route_v2(cp, [M.mk(o) for o in bag], M.mk(no), now=now)
        except Exception:
            M.patch_off()
            out["simfail"] = True
            return out
        M.patch_off()
        set_committed_pickup_tolerance(None)

        inf_on = M.is_fallback(plan_on)
        ln, oid_on = M.committed_late_max(plan_on, ck_by_oid)
        if lo is None or ln is None:
            out["off_nolate"] = True
            out["coeffs"].append((coeff, "nolate", 0.0, None, inf_on))
            continue
        delta = lo - ln  # >0 = ON reduces committed-late
        if abs(delta) < BORDERLINE_MIN:
            out["coeffs"].append((coeff, "border", 0.0, None, inf_on))
        elif delta > 0:
            out["coeffs"].append((coeff, "red", delta, None, inf_on))
        else:
            detail = dict(oid=no.get("order_id"), now=d.get("now"), day=day,
                          off_late=round(lo, 2), on_late=round(ln, 2),
                          delta=round(delta, 2), worst_oid=oid_on, tol=tol,
                          ewma=(round(ewma, 2) if ewma is not None else None))
            out["coeffs"].append((coeff, "regr", -delta, detail, inf_on))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coeffs", default="50,100,200")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dump-regr", default="")
    args = ap.parse_args()
    coeffs = [int(x) for x in args.coeffs.split(",")]

    recs, filt = M.load_records()
    print("=" * 100)
    print("N5-S2 VALIDATION REPLAY (PARALLEL) — committed-pickup punctuality penalty (OFF vs ON)")
    print("Metric: committed-pickup lateness in PLAN (planned pickup_at - czas_kuriera), max per bag")
    print("=" * 100)
    print(f"capture lines:            {filt['total']}")
    print(f"in-window (14-16.06):     {filt['window']}")
    print(f"FILTERED (ready-now>65m): {filt['filtered']}  (far czasówki not proposed)")
    print(f"no committed bag pickup:  {filt['no_committed']}  (skipped — penalty has no target)")
    print(f"QUALIFIED: {filt['qualified']}")
    by_oid = M.build_ewma_index()
    print(f"shadow EWMA index order_ids in-window: {len(by_oid)}")
    print(f"workers: {args.workers}  coeffs: {coeffs}\n", flush=True)

    with Pool(args.workers, initializer=_init, initargs=(by_oid, coeffs)) as pool:
        results = pool.map(_process, recs, chunksize=16)

    # aggregate per coeff per day
    # stats[coeff][day] = dict(pop, red[], regr[], regrlist[], inf_on, inf_off, border, simfail, off_nolate)
    stats = {c: defaultdict(lambda: dict(pop=0, red=[], regr=[], regrlist=[],
             infeasible_on=0, infeasible_off=0, border=0, simfail=0, off_nolate=0))
             for c in coeffs}
    for r in results:
        day = r["day"]
        if r.get("skip"):
            continue
        if r["simfail"]:
            for c in coeffs:
                stats[c][day]["simfail"] += 1
            continue
        for c in coeffs:
            stats[c][day]["pop"] += 1
            if r["infeasible_off"]:
                stats[c][day]["infeasible_off"] += 1
        for (coeff, kind, val, detail, inf_on) in r["coeffs"]:
            S = stats[coeff][day]
            if inf_on:
                S["infeasible_on"] += 1
            if kind == "nolate":
                S["off_nolate"] += 1
            elif kind == "border":
                S["border"] += 1
            elif kind == "red":
                S["red"].append(val)
            elif kind == "regr":
                S["regr"].append(val)
                if detail:
                    S["regrlist"].append(detail)

    med, p90 = M.med, M.p90
    all_regr_dump = []
    for coeff in coeffs:
        per_day = stats[coeff]
        print("\n" + "#" * 100)
        print(f"### COEFF = {coeff}")
        print("#" * 100)
        print(f"{'day':<11}{'pop':>7}{'reduced':>9}{'regr':>6}{'red med/p90':>16}"
              f"{'red sum':>10}{'regr med/p90/max':>20}{'regr sum':>10}"
              f"{'INF_on':>8}{'INF_off':>8}{'border':>8}{'simfail':>8}")
        Tred, Tregr, Tregrlist = [], [], []
        T = defaultdict(float)
        for day in DAYS:
            P = per_day[day]
            print(f"{day:<11}{P['pop']:>7}{len(P['red']):>9}{len(P['regr']):>6}"
                  f"{(str(round(med(P['red']),1))+'/'+str(round(p90(P['red']),1))):>16}"
                  f"{round(sum(P['red']),1):>10}"
                  f"{(str(round(med(P['regr']),1))+'/'+str(round(p90(P['regr']),1))+'/'+str(round(max(P['regr']) if P['regr'] else 0,1))):>20}"
                  f"{round(sum(P['regr']),1):>10}{P['infeasible_on']:>8}{P['infeasible_off']:>8}"
                  f"{P['border']:>8}{P['simfail']:>8}")
            for k in ("pop", "infeasible_on", "infeasible_off", "border", "simfail", "off_nolate"):
                T[k] += P[k]
            Tred += P['red']; Tregr += P['regr']; Tregrlist += P['regrlist']
        print("-" * 100)
        print(f"{'TOTAL':<11}{int(T['pop']):>7}{len(Tred):>9}{len(Tregr):>6}"
              f"{(str(round(med(Tred),1))+'/'+str(round(p90(Tred),1))):>16}"
              f"{round(sum(Tred),1):>10}"
              f"{(str(round(med(Tregr),1))+'/'+str(round(p90(Tregr),1))+'/'+str(round(max(Tregr) if Tregr else 0,1))):>20}"
              f"{round(sum(Tregr),1):>10}{int(T['infeasible_on']):>8}{int(T['infeasible_off']):>8}"
              f"{int(T['border']):>8}{int(T['simfail']):>8}")
        net = round(sum(Tred) - sum(Tregr), 1)
        print(f"\n  COEFF {coeff} SUMMARY:")
        print(f"    population (>=1 committed bag pickup):   {int(T['pop'])}")
        print(f"    committed-late REDUCED:  {len(Tred)} decisions, sum {round(sum(Tred),0)} min "
              f"(median {round(med(Tred),1)}, p90 {round(p90(Tred),1)})")
        print(f"    committed-late INCREASED (REGRESSION):   {len(Tregr)} decisions, sum {round(sum(Tregr),1)} min "
              f"(median {round(med(Tregr),1)}, p90 {round(p90(Tregr),1)}, max {round(max(Tregr) if Tregr else 0,1)})")
        print(f"    NET committed-late saved (red - regr):   {net} min")
        print(f"    INFEASIBLE/fallback under ON: {int(T['infeasible_on'])} "
              f"(OFF baseline: {int(T['infeasible_off'])})  [delta {int(T['infeasible_on'])-int(T['infeasible_off'])}]")
        print(f"    borderline |delta|<{BORDERLINE_MIN}min (no real change): {int(T['border'])}")
        if Tregrlist:
            print(f"\n  --- TOP REGRESSIONS coeff={coeff} (committed pickup later under ON) ---")
            for r in sorted(Tregrlist, key=lambda x: x['delta'])[:15]:
                print(f"    {r['day']} new_oid={r['oid']} worst_committed_oid={r['worst_oid']} "
                      f"OFF_late={r['off_late']} ON_late={r['on_late']} delta={r['delta']} "
                      f"tol={r['tol']} ewma={r['ewma']}")
        all_regr_dump.append(dict(coeff=coeff, regressions=Tregrlist, net=net,
                                  reduced=len(Tred), regr=len(Tregr),
                                  red_sum=round(sum(Tred), 1), regr_sum=round(sum(Tregr), 1),
                                  inf_on=int(T['infeasible_on']), inf_off=int(T['infeasible_off'])))

    if args.dump_regr:
        with open(args.dump_regr, "w") as fh:
            json.dump(all_regr_dump, fh, indent=2, ensure_ascii=False)
        print(f"\nregression dump -> {args.dump_regr}")


if __name__ == "__main__":
    main()
