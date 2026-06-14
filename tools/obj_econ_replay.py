#!/usr/bin/env python3
"""obj_econ_replay — replay EKONOMICZNY (NET=PLN) na danych historycznych. (E4, 2026-06-14)

route_metrics.compute_plan_metrics liczy TYLKO czas (span/idle/thermal/R6) — zero
kilometrów i zero złotówek. Ten moduł dokłada brakujący wymiar ekonomiczny do
harnessu replay, żeby każdy wariant funkcji kosztu dało się porównać po NET PLN
(a nie tylko po SLA). Warunek bezpiecznego flipa (E2 i przyszłe zmiany objective).

Pipeline (na rekord obj_replay_capture.jsonl — 100% wierne wejścia solvera):
  1. re-solve plan: simulate_bag_route_v2 (jak obj_harness.run_capture_record),
  2. odtwórz PEŁNĄ trasę: plan.sequence to TYLKO delivery order
     (route_simulator_v2:191) → łączymy węzły pickup (z pickup_at/arrival_at) i
     delivery (z predicted_delivered_at), sortujemy po czasie = realna kolejność
     odwiedzania kuriera,
  3. policz total_km / deadhead_km (haversine × HAVERSINE_ROAD_FACTOR_BIALYSTOK
     1.37). Deadhead = nogi do węzła PICKUP (dojazd "po jedzenie", kurier pusty —
     to jest "powrót jałowy"),
  4. mapuj na PLN stałymi z pln_objective: koszt_km·km + 14·(REALNE breach z
     planu) + opp_rate·idle. (Używamy REALNEGO r6_breach_count z re-solve, nie
     logitu P(breach) — replay daje faktyczny wynik planu.)

Porównanie wariantów A/B (wzorzec obj_harness): puść `run` pod configiem A → a.json,
zmień config (env/flaga/override) → `run` → b.json, potem `diff --a --b`.

OFFLINE, read-only, fail-soft per rekord. NIE dotyka produkcji (zero zapisu poza
--out do /tmp). Capture (cid kuriera nie jest w logu) → km_cost konserwatywnie
PLN_KM_COST_FIRMOWE (0.90); override przez --km-cost.
"""
import argparse
import json
import statistics
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import pln_objective as PLN  # noqa: E402
from dispatch_v2.osrm_client import haversine  # noqa: E402
from dispatch_v2.route_metrics import compute_plan_metrics  # noqa: E402
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2  # noqa: E402
from dispatch_v2.tools.obj_harness import (  # noqa: E402
    _dt,
    _ordersim_from_capture,
    load_capture,
)

ROAD = C.HAVERSINE_ROAD_FACTOR_BIALYSTOK  # 1.37


def _aware(dt):
    """datetime → aware UTC; None gdy nie-datetime (do sortowania węzłów)."""
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _valid_coords(c):
    """True gdy realne coords; odrzuca None/(0,0) sentinel (paczki/niegeokodowane)."""
    return bool(c) and len(c) == 2 and not (abs(c[0]) < 1e-9 and abs(c[1]) < 1e-9)


def _km_breakdown(plan, courier_pos, coords_by_oid):
    """Pełna trasa z timestampów planu → total_km / deadhead_km / loaded_km.

    Deadhead = nogi, których CELEM jest węzeł pickup (kurier jedzie po jedzenie
    pusty — dojazd początkowy courier→pickup oraz powroty delivery→pickup).
    None gdy brak courier_pos albo brak węzłów z czasem (nie zgadujemy trasy).
    """
    if not _valid_coords(courier_pos):
        return None
    pickup_at = getattr(plan, "pickup_at", None) or {}
    arrival_at = getattr(plan, "arrival_at", None) or {}
    deliv_at = getattr(plan, "predicted_delivered_at", None) or {}
    seq = getattr(plan, "sequence", None) or []

    nodes = []  # (czas_aware, coords, kind)
    for oid, t in pickup_at.items():
        c = (coords_by_oid.get(oid) or {}).get("pickup")
        a = _aware(t if t is not None else arrival_at.get(oid))
        if c is None or a is None:
            continue
        if not _valid_coords(c):
            return None  # fikcyjny (0,0)/sentinel → nie licz częściowej trasy (skip rekord)
        nodes.append((a, tuple(c), "pickup"))
    for oid in seq:
        c = (coords_by_oid.get(oid) or {}).get("delivery")
        a = _aware(deliv_at.get(oid))
        if c is None or a is None:
            continue
        if not _valid_coords(c):
            return None
        nodes.append((a, tuple(c), "delivery"))
    if not nodes:
        return None

    nodes.sort(key=lambda x: x[0])
    prev = tuple(courier_pos)
    total = dead = 0.0
    for _a, c, kind in nodes:
        leg = haversine(prev, c) * ROAD
        total += leg
        if kind == "pickup":
            dead += leg
        prev = c
    return {
        "total_km": round(total, 3),
        "deadhead_km": round(dead, 3),
        "loaded_km": round(total - dead, 3),
        "n_nodes": len(nodes),
    }


def run_capture_econ(rec, km_cost, food_age_on=False):
    """Replay 1 rekordu capture → metryki czasowe (route_metrics) + ekonomiczne (PLN).
    food_age_on=True → re-solve z wymuszonym członem food-age (challenger A/B)."""
    out = {"case_id": rec.get("order_id"), "now": rec.get("now")}
    try:
        bag = [_ordersim_from_capture(o) for o in rec.get("bag", [])]
        new = _ordersim_from_capture(rec["new_order"])
        coords_by_oid = {}
        for s in bag + [new]:
            coords_by_oid[s.order_id] = {
                "pickup": tuple(s.pickup_coords),
                "delivery": tuple(s.delivery_coords),
            }
        dwell_p = rec.get("dwell_pickup")
        _pos = tuple(rec.get("courier_pos") or ())
        _kw = dict(now=_dt(rec.get("now")), dwell_pickup=dwell_p,
                   dwell_dropoff=rec.get("dwell_dropoff"))
        if food_age_on:
            with C.food_age_override(True):
                plan = simulate_bag_route_v2(_pos, bag, new, **_kw)
        else:
            plan = simulate_bag_route_v2(_pos, bag, new, **_kw)
        m = compute_plan_metrics(plan, dwell_p)
        km = _km_breakdown(plan, rec.get("courier_pos"), coords_by_oid)
        if km is None:
            out["skipped"] = "no_km"
            return out
        rate = PLN.opp_rate(_dt(rec.get("now")), None)
        km_zl = km_cost * km["total_km"]
        breach_zl = PLN.PLN_BREACH_COST * m["r6_breach_count"]
        idle_zl = rate * m["idle_total_min"]
        out.update({
            "strategy": plan.strategy,
            "total_km": km["total_km"],
            "deadhead_km": km["deadhead_km"],
            "loaded_km": km["loaded_km"],
            "r6_breach_count": m["r6_breach_count"],
            "idle_min": m["idle_total_min"],
            "span_min": m["route_span_min"],
            "opp_rate": rate,
            "pln_km_cost": round(km_zl, 2),
            "pln_breach_cost": round(breach_zl, 2),
            "pln_idle_cost": round(idle_zl, 2),
            "pln_cost": round(km_zl + breach_zl + idle_zl, 2),
        })
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _agg(vals):
    if not vals:
        return None
    return {
        "sum": round(sum(vals), 2),
        "median": round(statistics.median(vals), 3),
        "mean": round(statistics.mean(vals), 3),
    }


def aggregate(cases):
    ok = [c for c in cases if "pln_cost" in c]

    def col(k):
        return [c[k] for c in ok if c.get(k) is not None]

    tot_km = sum(col("total_km"))
    dead_km = sum(col("deadhead_km"))
    return {
        "n_total": len(cases),
        "n_ok": len(ok),
        "n_error": sum(1 for c in cases if c.get("error")),
        "n_skipped": sum(1 for c in cases if c.get("skipped")),
        "total_km": _agg(col("total_km")),
        "deadhead_km": _agg(col("deadhead_km")),
        "loaded_km": _agg(col("loaded_km")),
        "deadhead_share": round(dead_km / tot_km, 4) if tot_km > 0 else None,
        "r6_breach_count": _agg(col("r6_breach_count")),
        "idle_min": _agg(col("idle_min")),
        "pln_cost": _agg(col("pln_cost")),
    }


def diff_reports(ra, rb):
    a = {c["case_id"]: c for c in ra["cases"] if "pln_cost" in c}
    b = {c["case_id"]: c for c in rb["cases"] if "pln_cost" in c}
    common = sorted(set(a) & set(b))
    better = worse = same = 0
    d_pln = d_km = d_dead = d_breach = 0.0
    for k in common:
        dp = b[k]["pln_cost"] - a[k]["pln_cost"]
        d_pln += dp
        d_km += b[k]["total_km"] - a[k]["total_km"]
        d_dead += b[k]["deadhead_km"] - a[k]["deadhead_km"]
        d_breach += b[k]["r6_breach_count"] - a[k]["r6_breach_count"]
        if dp < -0.01:
            better += 1
        elif dp > 0.01:
            worse += 1
        else:
            same += 1
    n = len(common)
    print(f"DIFF A→B  (common cases n={n})")
    print(f"  B tańszy (lepszy): {better}   B drożej (gorszy): {worse}   bez zmian: {same}")
    print(f"  Σ ΔPLN_cost: {d_pln:+.2f} zł   Σ Δtotal_km: {d_km:+.2f}   "
          f"Σ Δdeadhead_km: {d_dead:+.2f}   Σ Δbreach: {d_breach:+.0f}")
    if n:
        print(f"  śr. ΔPLN/case: {d_pln/n:+.3f} zł   śr. Δkm/case: {d_km/n:+.3f}")
    return {"n": n, "better": better, "worse": worse, "same": same,
            "d_pln": round(d_pln, 2), "d_km": round(d_km, 2),
            "d_deadhead": round(d_dead, 2), "d_breach": d_breach}


def _print_agg(agg):
    print(f"  n_ok={agg['n_ok']}/{agg['n_total']}  err={agg['n_error']}  skip={agg['n_skipped']}")
    for k in ("total_km", "deadhead_km", "loaded_km", "idle_min", "r6_breach_count", "pln_cost"):
        v = agg.get(k)
        if v:
            print(f"  {k:16s} sum={v['sum']:>10}  median={v['median']:>8}  mean={v['mean']:>8}")
    print(f"  deadhead_share={agg['deadhead_share']}")


def main():
    ap = argparse.ArgumentParser(description="obj_econ_replay — replay ekonomiczny NET=PLN (E4)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="replay econ z capture jsonl, raport JSON + agregat")
    r.add_argument("--capture", default="/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl")
    r.add_argument("--limit", type=int, default=0, help="tylko ostatnie N rekordów (0=wszystkie)")
    r.add_argument("--km-cost", type=float, default=PLN.PLN_KM_COST_FIRMOWE, help="koszt zł/km (default 0.90 firmowe)")
    r.add_argument("--food-age", action="store_true",
                   help="challenger: re-solve z wymuszonym członem food-age (A/B vs baseline)")
    r.add_argument("--out", default="/tmp/econ_report.json")
    d = sub.add_parser("diff", help="porównaj dwa raporty econ (A=baseline, B=wariant)")
    d.add_argument("--a", required=True)
    d.add_argument("--b", required=True)
    args = ap.parse_args()

    if args.cmd == "run":
        recs = load_capture(args.capture, args.limit)
        cases = [run_capture_econ(rc, args.km_cost, args.food_age) for rc in recs]
        agg = aggregate(cases)
        report = {"source": args.capture, "km_cost": args.km_cost,
                  "variant": "food_age_on" if args.food_age else "baseline",
                  "n_cases": len(recs), "aggregate": agg, "cases": cases}
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"AGREGAT  (km_cost={args.km_cost} zł/km)")
        _print_agg(agg)
        print(f"\nraport → {args.out}")
    elif args.cmd == "diff":
        with open(args.a) as f:
            ra = json.load(f)
        with open(args.b) as f:
            rb = json.load(f)
        diff_reports(ra, rb)


if __name__ == "__main__":
    main()
