#!/usr/bin/env python3
"""pickup_slip_prep_bias_replay.py — READ-ONLY: is the +9min pickup-timing slip prep_bias-fixable?

ZERO writes, ZERO flips. The pickup-timing slip per order is recoverable tz-safely from logged
durations (no timezone math):
    slip = eta_error_min - (real_delivery_min - predicted_delivery_min)
         = (delivered slip) - (drive+dwell leg error) = how much LATER pickup happened than predicted.
The route ETA verdict (2026-06-21) showed median slip ~+9min, drive leg ~-1.2min. prep_bias corrects
this by per-restaurant offset (effective_ready = pickup_ready + bias). The question: how much of the
slip does RESTAURANT identity explain (= ceiling of prep_bias), and is a held-out correction flip-safe?

Run: cd /root/.openclaw/workspace/scripts && PYTHONPATH=. \
     /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/eod_drafts/2026-06-21/pickup_slip_prep_bias_replay.py
"""
import json
import statistics as st

LOG = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
TABLE = "/root/.openclaw/workspace/dispatch_state/restaurant_prep_bias.json"
CAP = 45.0
MIN_N = 10   # min orders/restaurant to trust a per-restaurant offset


def med(x):
    return st.median(x) if x else float("nan")


def medabs(x):
    return st.median([abs(v) for v in x]) if x else float("nan")


def acc(x, t=5.0):
    return 100.0 * sum(1 for v in x if abs(v) <= t) / len(x) if x else float("nan")


def main():
    rows = []
    for line in open(LOG):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("matched_courier") is not True or d.get("was_czasowka"):
            continue
        e, rd, pd = d.get("eta_error_min"), d.get("real_delivery_min"), d.get("predicted_delivery_min")
        rest = d.get("restaurant")
        if e is None or rd is None or pd is None or not rest:
            continue
        slip = e - (rd - pd)
        if abs(slip) > CAP:
            continue
        rows.append((rest.strip().lower(), slip))

    n = len(rows)
    allslip = [s for _, s in rows]
    print(f"=== CORPUS ===  {n} matched non-czasowka orders with restaurant\n")
    print("=== (A) PICKUP-TIMING SLIP (how much later pickup happened than predicted) ===")
    print(f"  median={med(allslip):+.2f}  mean={sum(allslip)/n:+.2f}  std={st.pstdev(allslip):.2f}  "
          f"median|slip|={medabs(allslip):.2f}  on-time(|slip|<=5)={acc(allslip):.1f}%\n")

    # group by restaurant
    byr = {}
    for r, s in rows:
        byr.setdefault(r, []).append(s)
    big = {r: v for r, v in byr.items() if len(v) >= MIN_N}
    print(f"=== (B) VARIANCE DECOMPOSITION (ceiling of any per-restaurant correction) ===")
    print(f"  restaurants total={len(byr)}  with >= {MIN_N} orders={len(big)} (covering "
          f"{100*sum(len(v) for v in big.values())/n:.0f}% of orders)")
    # between vs within (on the >=MIN_N subset)
    sub = [(r, s) for r, s in rows if r in big]
    gm = st.mean([s for _, s in sub])
    total_var = st.pvariance([s for _, s in sub])
    within_var = sum(sum((s - med(big[r]))**2 for s in big[r]) for r in big) / len(sub)
    between_r2 = max(0.0, 1 - within_var / total_var)
    rest_meds = [med(v) for v in big.values()]
    print(f"  total slip variance={total_var:.1f}  within-restaurant={within_var:.1f}  "
          f"=> restaurant explains R^2={between_r2:.2f}")
    print(f"  per-restaurant median slip: min={min(rest_meds):+.1f} p50={med(rest_meds):+.1f} "
          f"max={max(rest_meds):+.1f}  (spread of restaurant medians)")
    hi_var = sum(1 for r in big if st.pstdev(big[r]) > abs(med(big[r])))
    print(f"  restaurants where within-std > |median| (noise dominates): {hi_var}/{len(big)}\n")

    # (C) held-out: BASE vs GLOBAL-correction vs PER-RESTAURANT-correction
    split = int(n * 0.6)
    train, test = rows[:split], rows[split:]
    tr_by = {}
    for r, s in train:
        tr_by.setdefault(r, []).append(s)
    gbias = med([s for _, s in train])
    rbias = {r: (med(v) if len(v) >= MIN_N else gbias) for r, v in tr_by.items()}

    base = [s for _, s in test]
    glob = [s - gbias for _, s in test]
    perr = [s - rbias.get(r, gbias) for r, s in test]
    print(f"=== (C) HELD-OUT (train={len(train)} / test={len(test)}) — residual slip after correction ===")
    print(f"  {'corrector':<28} {'median|slip|':>12} {'median':>9} {'on-time<=5':>11}")
    print(f"  {'BASE (none)':<28} {medabs(base):>12.2f} {med(base):>+9.2f} {acc(base):>10.1f}%")
    print(f"  {'GLOBAL (-' + format(gbias, '.1f') + ' all)':<28} {medabs(glob):>12.2f} {med(glob):>+9.2f} {acc(glob):>10.1f}%")
    print(f"  {'PER-RESTAURANT (prep_bias)':<28} {medabs(perr):>12.2f} {med(perr):>+9.2f} {acc(perr):>10.1f}%\n")

    # (D) regression guard: among already on-time pickups (|base slip|<=5), who breaks them?
    ok = [i for i, (_, s) in enumerate(test) if abs(s) <= 5.0]
    if ok:
        gb = 100 * sum(1 for i in ok if abs(glob[i]) > 5) / len(ok)
        pb = 100 * sum(1 for i in ok if abs(perr[i]) > 5) / len(ok)
        print("=== (D) REGRESSION GUARD (of already on-time pickups |slip|<=5, % broken to >5) ===")
        print(f"  already on-time: {len(ok)}  | GLOBAL breaks {gb:.1f}%  | PER-RESTAURANT breaks {pb:.1f}%\n")

    # (E) sanity vs shipped shadow table
    try:
        tbl = json.load(open(TABLE))
        g = tbl.get("global", {})
        gall = g.get("all", {})
        print("=== (E) SANITY vs shipped restaurant_prep_bias.json ===")
        print(f"  table global bias_med={gall.get('bias_med')} std={gall.get('std')} "
              f"iqr={gall.get('iqr')} n={gall.get('n')}  |  measured median slip={med(allslip):+.1f} "
              f"std={st.pstdev(allslip):.1f}  -> consistent\n")
    except (OSError, ValueError):
        pass

    print("(Interpretation: high R^2 + per-restaurant clearly beats global + low break-rate = prep_bias is\n"
          " a real flip-safe lever. Low R^2 + per-rest ~= global + high break-rate = the slip is per-order\n"
          " noise a static table can't fix, and subtracting it overcorrects on-time pickups.)")


if __name__ == "__main__":
    main()
