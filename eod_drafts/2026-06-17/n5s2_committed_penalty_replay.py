#!/usr/bin/env python3
"""
N5 KROK 2 VALIDATION REPLAY — committed-pickup punctuality penalty (READ-ONLY).
2026-06-17. Flag OFF in prod; this replays OFF vs ON on 14-16.06 BEFORE flip.

WHAT N5-S2 DOES (coded, flag OFF):
  - tsp_solver.solve_tsp_with_constraints gets pickup_committed_penalties →
    SetCumulVarSoftUpperBound on pickup nodes whose ref has czas_kuriera_warsaw.
    SOFT (never INFEASIBLE).
  - route_simulator_v2._ortools_plan builds it when decision_flag(
    "ENABLE_OBJ_COMMITTED_PICKUP_PENALTY"): for each pickup-node ref with
    czas_kuriera_warsaw, bound_min=(czas_kuriera-now)/min + tolerance;
    coeff=C.OBJ_COMMITTED_PICKUP_PENALTY_COEFF.
  - tolerance: route_simulator_v2.set_committed_pickup_tolerance(min); pipeline
    sets 10 when loadgov_ewma>=4.5 (shortage) else 5. Here set per-decision from
    best.loadgov_load_ewma (shadow_decisions), None->strict 5.0 (prod default).

HOW WE TOGGLE: monkeypatch dispatch_v2.common.decision_flag to return True for
'ENABLE_OBJ_COMMITTED_PICKUP_PENALTY' on the ON-run, original on OFF-run. We set
C.OBJ_COMMITTED_PICKUP_PENALTY_COEFF before each run (prod reads it via getattr).

ORDERSIM: each committed bag-order gets sim.czas_kuriera_warsaw=<from bag dict>
(prod _ortools_plan reads node.ref.czas_kuriera_warsaw); without it the bound is
not built. new_order also carries czas_kuriera_warsaw if present.

FILTER (Adrian): drop candidates where (new_order pickup_ready - now) > 65 min
(far czasówki are not proposed). Reported.

METRICS per candidate (OFF vs ON), minutes; planned pickup_at - committed:
  committed_late_max = max over committed pickups of (plan.pickup_at[oid] - ck)
  WIN  = OFF.committed_late_max - ON.committed_late_max > 0 (ON reduces lateness)
  REGR = < 0 (ON pushes a committed pickup later — bound moved geometry)
  INFEASIBLE/fallback: ON yields no plan OR strategy contains 'fallback'/'rejected'
COEFF sweep: {50,100,200}.
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
BORDERLINE_MIN = 1.0   # |delta| below this = noise / no real change


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
    """Build OrderSim; attach czas_kuriera_warsaw as ad-hoc attr (prod reads it)."""
    sim = OrderSim(
        order_id=str(o.get("order_id")),
        pickup_coords=tuple(o["pickup_coords"]),
        delivery_coords=tuple(o["delivery_coords"]) if o.get("delivery_coords") else tuple(o["pickup_coords"]),
        picked_up_at=pts(o.get("picked_up_at")),
        status=o.get("status") or "assigned",
        pickup_ready_at=pts(o.get("pickup_ready_at")),
    )
    ckw = o.get("czas_kuriera_warsaw")
    if ckw is not None and str(ckw).strip() not in ("", "None", "null", "NULL"):
        sim.czas_kuriera_warsaw = ckw   # prod: getattr(ref, 'czas_kuriera_warsaw')
    return sim


def committed_late_max(plan, ck_by_oid):
    """max over committed orders (czas_kuriera set) of planned pickup minus committed."""
    if plan is None or not getattr(plan, "pickup_at", None):
        return (None, None)
    worst = None
    worst_oid = None
    for oid, ck in ck_by_oid.items():
        pat = plan.pickup_at.get(oid)
        if pat is None:
            continue  # already picked / not in this plan
        late = (pat - ck).total_seconds() / 60.0
        if worst is None or late > worst:
            worst = late
            worst_oid = oid
    return (worst, worst_oid)


def is_fallback(plan):
    if plan is None:
        return True
    s = (getattr(plan, "strategy", "") or "").lower()
    return ("fallback" in s) or ("rejected" in s) or (not getattr(plan, "sequence", None))


def build_ewma_index():
    """(order_id) -> list of (utc_ts, ewma) from shadow_decisions in-window."""
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
    """nearest ts within 5s; else None (->strict tol, prod default)."""
    cands = by_oid.get(str(oid))
    if not cands or cnow is None:
        return None
    best = min(cands, key=lambda x: abs((x[0] - cnow).total_seconds()))
    if abs((best[0] - cnow).total_seconds()) < 5.0:
        return best[1]
    return None


# ---- flag toggling ---------------------------------------------------------
_ORIG_FLAG = C.decision_flag


def patch_on():
    C.decision_flag = (lambda name: True if name == "ENABLE_OBJ_COMMITTED_PICKUP_PENALTY"
                       else _ORIG_FLAG(name))


def patch_off():
    C.decision_flag = _ORIG_FLAG


def load_records():
    """Yield qualifying replay records with committed bag pickups."""
    recs = []
    n_total = n_window = n_filtered = n_no_committed = 0
    with open(CAP) as f:
        for line in f:
            n_total += 1
            try:
                d = json.loads(line)
            except Exception:
                continue
            day = wday(d.get("now"))
            if day not in DAYS:
                continue
            n_window += 1
            no = d.get("new_order") or {}
            now = pts(d.get("now"))
            ready = pts(no.get("pickup_ready_at"))
            # FILTER: far czasówki on the NEW order are not real proposals
            if now and ready and (ready - now).total_seconds() / 60.0 > WINDOW_MAX_MIN:
                n_filtered += 1
                continue
            bag = d.get("bag") or []
            ck_orders = [o for o in bag if has_ck(o)]
            if not ck_orders:
                n_no_committed += 1
                continue
            recs.append((day, d, now, no, bag))
    return recs, dict(total=n_total, window=n_window, filtered=n_filtered,
                      no_committed=n_no_committed, qualified=len(recs))


def run_coeff(recs, by_oid, coeff):
    """Replay every qualifying record OFF vs ON for one coeff value."""
    C.OBJ_COMMITTED_PICKUP_PENALTY_COEFF = float(coeff)
    per_day = defaultdict(lambda: dict(
        pop=0, changed=0, red=[], regr=[], regrlist=[],
        infeasible_on=0, infeasible_off=0, borderline=0,
        simfail=0, off_nolate=0))

    for (day, d, now, no, bag) in recs:
        P = per_day[day]
        P["pop"] += 1
        cp = tuple(d["courier_pos"]) if d.get("courier_pos") else None
        if cp is None:
            continue
        ck_by_oid = {}
        for o in bag:
            if has_ck(o):
                ck = pts(o.get("czas_kuriera_warsaw"))
                if ck:
                    ck_by_oid[str(o.get("order_id"))] = ck
        if not ck_by_oid:
            continue

        # tolerance = load-aware per decision (prod: pipeline sets it)
        ewma = ewma_for(by_oid, d.get("order_id"), now)
        tol = TOL_LOOSE if (ewma is not None and ewma >= EWMA_THRESHOLD) else TOL_STRICT

        bag_objs = [mk(o) for o in bag]
        new_obj = mk(no)

        # OFF run
        patch_off()
        set_committed_pickup_tolerance(None)
        try:
            plan_off = simulate_bag_route_v2(cp, bag_objs, new_obj, now=now)
        except Exception:
            P["simfail"] += 1
            continue

        # ON run (rebuild OrderSim fresh — defensive against any in-place mutation)
        bag_objs2 = [mk(o) for o in bag]
        new_obj2 = mk(no)
        patch_on()
        set_committed_pickup_tolerance(tol)
        try:
            plan_on = simulate_bag_route_v2(cp, bag_objs2, new_obj2, now=now)
        except Exception:
            patch_off()
            P["simfail"] += 1
            continue
        patch_off()
        set_committed_pickup_tolerance(None)

        if is_fallback(plan_off):
            P["infeasible_off"] += 1
        if is_fallback(plan_on):
            P["infeasible_on"] += 1

        lo, _ = committed_late_max(plan_off, ck_by_oid)
        ln, oid_on = committed_late_max(plan_on, ck_by_oid)
        if lo is None or ln is None:
            P["off_nolate"] += 1
            continue
        delta = lo - ln  # >0 = ON reduces committed-late
        if abs(delta) < BORDERLINE_MIN:
            P["borderline"] += 1
            continue
        if delta > 0:
            P["red"].append(delta)
            P["changed"] += 1
        else:
            P["regr"].append(-delta)
            P["changed"] += 1
            P["regrlist"].append(dict(
                oid=no.get("order_id"), now=d.get("now"), day=day,
                off_late=round(lo, 2), on_late=round(ln, 2),
                delta=round(delta, 2), worst_oid=oid_on, tol=tol,
                ewma=(round(ewma, 2) if ewma is not None else None)))
    return per_day


def med(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * 0.9))] if xs else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coeffs", default="50,100,200")
    ap.add_argument("--dump-regr", default="")
    args = ap.parse_args()
    coeffs = [int(x) for x in args.coeffs.split(",")]

    recs, filt = load_records()
    print("=" * 100)
    print("N5-S2 VALIDATION REPLAY — committed-pickup punctuality penalty (OFF vs ON)")
    print("Metric: committed-pickup lateness in PLAN (planned pickup_at - czas_kuriera), max per bag")
    print("=" * 100)
    print(f"capture lines:            {filt['total']}")
    print(f"in-window (14-16.06):     {filt['window']}")
    print(f"FILTERED (ready-now>65m): {filt['filtered']}  (far czasówki not proposed)")
    print(f"no committed bag pickup:  {filt['no_committed']}  (skipped — penalty has no target)")
    print(f"QUALIFIED (>=1 committed bag pickup, real proposal window): {filt['qualified']}")

    by_oid = build_ewma_index()
    print(f"shadow EWMA index order_ids in-window: {len(by_oid)}")

    all_regr_dump = []
    for coeff in coeffs:
        per_day = run_coeff(recs, by_oid, coeff)
        print("\n" + "#" * 100)
        print(f"### COEFF = {coeff}")
        print("#" * 100)
        hdr = (f"{'day':<11}{'pop':>7}{'changed':>9}{'reduced':>9}{'regr':>6}"
               f"{'red med/p90':>16}{'red sum':>10}{'regr med/p90/max':>20}"
               f"{'regr sum':>10}{'INF_on':>8}{'INF_off':>8}{'border':>8}{'simfail':>8}")
        print(hdr)
        T = defaultdict(float)
        Tred = []
        Tregr = []
        Tregrlist = []
        for day in DAYS:
            P = per_day[day]
            print(f"{day:<11}{P['pop']:>7}{P['changed']:>9}{len(P['red']):>9}{len(P['regr']):>6}"
                  f"{(str(round(med(P['red']),1))+'/'+str(round(p90(P['red']),1))):>16}"
                  f"{round(sum(P['red']),1):>10}"
                  f"{(str(round(med(P['regr']),1))+'/'+str(round(p90(P['regr']),1))+'/'+str(round(max(P['regr']) if P['regr'] else 0,1))):>20}"
                  f"{round(sum(P['regr']),1):>10}{P['infeasible_on']:>8}{P['infeasible_off']:>8}"
                  f"{P['borderline']:>8}{P['simfail']:>8}")
            for k in ("pop", "infeasible_on", "infeasible_off", "borderline", "simfail", "off_nolate"):
                T[k] += P[k]
            Tred += P['red']
            Tregr += P['regr']
            Tregrlist += P['regrlist']
        print("-" * 100)
        print(f"{'TOTAL':<11}{int(T['pop']):>7}{len(Tred)+len(Tregr):>9}{len(Tred):>9}{len(Tregr):>6}"
              f"{(str(round(med(Tred),1))+'/'+str(round(p90(Tred),1))):>16}"
              f"{round(sum(Tred),1):>10}"
              f"{(str(round(med(Tregr),1))+'/'+str(round(p90(Tregr),1))+'/'+str(round(max(Tregr) if Tregr else 0,1))):>20}"
              f"{round(sum(Tregr),1):>10}{int(T['infeasible_on']):>8}{int(T['infeasible_off']):>8}"
              f"{int(T['borderline']):>8}{int(T['simfail']):>8}")
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
        print(f"    borderline |delta|<{BORDERLINE_MIN}min (no real change): {int(T['borderline'])}")
        if Tregrlist:
            print(f"\n  --- TOP REGRESSIONS coeff={coeff} (committed pickup later under ON) ---")
            for r in sorted(Tregrlist, key=lambda x: x['delta'])[:15]:
                print(f"    {r['day']} new_oid={r['oid']} worst_committed_oid={r['worst_oid']} "
                      f"OFF_late={r['off_late']} ON_late={r['on_late']} delta={r['delta']} "
                      f"tol={r['tol']} ewma={r['ewma']}")
        all_regr_dump.append(dict(coeff=coeff, regressions=Tregrlist,
                                  net=net, reduced=len(Tred), regr=len(Tregr),
                                  red_sum=round(sum(Tred), 1), regr_sum=round(sum(Tregr), 1),
                                  inf_on=int(T['infeasible_on']), inf_off=int(T['infeasible_off'])))

    if args.dump_regr:
        with open(args.dump_regr, "w") as fh:
            json.dump(all_regr_dump, fh, indent=2, ensure_ascii=False)
        print(f"\nregression dump -> {args.dump_regr}")


if __name__ == "__main__":
    main()
