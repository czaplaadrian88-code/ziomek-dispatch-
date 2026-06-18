#!/usr/bin/env python3
"""Objective backtest over obj_replay_capture — the REAL Ziomek lever.

Confirmed mechanism: live plan = plan_recheck designation-sweep over
simulate_bag_route_v2 (full TSP), selected by (sla_violations, total_duration)
= MAKESPAN. NOT incremental insert. So the lever is the OBJECTIVE, not re-TSP.

Per real decision (bag>=2), 2 engine objectives, scored by compute_plan_metrics:
  BASE    = makespan (food-age OFF)         == today's prod
  FOODAGE = makespan + food-age hard-SLA ON == Option B (21.06-pending sprint)

Also (cheap, no extra sim):
  - committed-order delivery LATENESS vs (czas_kuriera + direct OSRM + dwell)
      -> sizes Option C (czasówka delivery-window gap)
  - far-bundle flag: a committed order's delivery >FAR_KM from centroid of the
      other deliveries -> sizes Option D (anti-bundle gate)

Segmented by bag size. Read-only.
Usage: policy_backtest_v2.py [--from D --to D --max N --min-bag K --workers W --far-km F]
"""
import argparse, json, math, sys, time
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict
from multiprocessing import Pool

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)
CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
OUTDIR = SCRIPTS + "/dispatch_v2/eod_drafts/2026-06-18"
SLA = 35
FAR_KM = 1.5
_ENG = {}


def _eng():
    if not _ENG:
        from dispatch_v2 import common as C
        import dispatch_v2.common as CC
        from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim
        from dispatch_v2.route_metrics import compute_plan_metrics
        from dispatch_v2 import osrm_client
        CC.V326_OR_TOOLS_TIME_LIMIT_MS = 200
        _ENG.update(C=C, CC=CC, sim=simulate_bag_route_v2, OrderSim=OrderSim,
                    metrics=compute_plan_metrics, osrm=osrm_client)
    return _ENG


def _dt(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except Exception:
        return None


def _mk(d, OrderSim):
    pc, dc = d.get("pickup_coords"), d.get("delivery_coords")
    if not pc or not dc or len(pc) != 2 or len(dc) != 2:
        return None
    o = OrderSim(d.get("order_id"), tuple(pc), tuple(dc),
                 _dt(d.get("picked_up_at")), d.get("status") or "assigned",
                 pickup_ready_at=_dt(d.get("pickup_ready_at")))
    if d.get("czas_kuriera_warsaw"):
        o.czas_kuriera_warsaw = d["czas_kuriera_warsaw"]
    return o


def _hav_km(a, b):
    R = 6371.0
    p = math.pi / 180
    dlat = (b[0] - a[0]) * p
    dlng = (b[1] - a[1]) * p
    la, lb = a[0] * p, b[0] * p
    h = math.sin(dlat / 2) ** 2 + math.cos(la) * math.cos(lb) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _scores(plan, metrics, dwell_p):
    m = metrics(plan, dwell_p or 1.0)
    return {
        "thermal": m.get("max_thermal_age_min"),
        "r6_cnt": m.get("r6_breach_count") or 0,
        "r6_max": m.get("r6_breach_max_min") or 0.0,
        "span": m.get("route_span_min"),
        "sla": plan.sla_violations or 0,
        "seq": list(plan.sequence),
        "deliv": {k: v.isoformat() for k, v in (plan.predicted_delivered_at or {}).items()},
    }


def run_one(d):
    try:
        E = _eng()
        OrderSim, sim, metrics, C, CC, osrm = (
            E["OrderSim"], E["sim"], E["metrics"], E["C"], E["CC"], E["osrm"])
        cp = d.get("courier_pos")
        now = _dt(d.get("now"))
        if not cp or len(cp) != 2 or now is None:
            return None
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        bag = [_mk(o, OrderSim) for o in d.get("bag") or []]
        no = _mk(d.get("new_order") or {}, OrderSim)
        if no is None or any(b is None for b in bag) or len(bag) < 2:
            return None
        dp, dd = d.get("dwell_pickup"), d.get("dwell_dropoff")
        kw = dict(now=now, sla_minutes=SLA)
        if dp is not None:
            kw["dwell_pickup"] = dp
        if dd is not None:
            kw["dwell_dropoff"] = dd

        all_orders = bag + [no]
        committed = {o.order_id: getattr(o, "czas_kuriera_warsaw", None)
                     for o in all_orders if getattr(o, "czas_kuriera_warsaw", None)}

        # far-bundle (Option D): committed deliv far from centroid of others' deliv
        far_bundle = False
        deliv = {o.order_id: o.delivery_coords for o in all_orders}
        for coid in committed:
            others = [c for oid, c in deliv.items() if oid != coid]
            if len(others) >= 1:
                cx = sum(c[0] for c in others) / len(others)
                cy = sum(c[1] for c in others) / len(others)
                if _hav_km(deliv[coid], (cx, cy)) > FAR_KM:
                    far_bundle = True

        CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = False
        p_base = sim(tuple(cp), bag, no, **kw)
        if p_base.strategy != "ortools":
            return None
        CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = True
        with C.food_age_override(True):
            p_fa = sim(tuple(cp), bag, no, **kw)
        CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = False

        sb, sf = _scores(p_base, metrics, dp), _scores(p_fa, metrics, dp)

        # committed delivery lateness (Option C) vs direct run, under BASE
        commit_late = []
        for coid, ck in committed.items():
            ckd = _dt(ck)
            dv = (sb["deliv"].get(coid))
            o = next((x for x in all_orders if x.order_id == coid), None)
            if ckd is None or dv is None or o is None:
                continue
            try:
                cell = osrm.table([o.pickup_coords], [o.delivery_coords])[0][0]
                direct = (cell.get("duration") or 0) / 60.0
            except Exception:
                direct = _hav_km(o.pickup_coords, o.delivery_coords) / 25.0 * 60.0
            if ckd.tzinfo is None:
                ckd = ckd.replace(tzinfo=timezone.utc)
            ideal = ckd + timedelta(minutes=direct + (dd or 3.5))
            late = (_dt(dv) - ideal).total_seconds() / 60.0
            commit_late.append(round(late, 1))

        return {
            "ts": (d.get("ts") or "")[:19], "bag": len(bag),
            "n_committed": len(committed), "far_bundle": far_bundle,
            "base": {k: sb[k] for k in ("thermal", "r6_cnt", "r6_max", "span", "sla", "seq")},
            "fa": {k: sf[k] for k in ("thermal", "r6_cnt", "r6_max", "span", "sla", "seq")},
            "commit_late": commit_late,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2026-06-04T00:00")
    ap.add_argument("--to", dest="to", default="2026-06-18T23:59")
    ap.add_argument("--max", type=int, default=3000)
    ap.add_argument("--min-bag", type=int, default=2)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()

    recs = []
    with open(CAPTURE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = (d.get("ts") or "")[:16]
            if ts < a.frm or ts > a.to or len(d.get("bag") or []) < a.min_bag:
                continue
            recs.append(d)
    elig = len(recs)
    if len(recs) > a.max:
        stride = len(recs) / a.max
        recs = [recs[int(i * stride)] for i in range(a.max)]
    sys.stderr.write(f"[bt2] eligible={elig} sampled={len(recs)} workers={a.workers}\n")
    sys.stderr.flush()

    R, errs, t0 = [], 0, time.time()
    with Pool(a.workers) as pool:
        for i, r in enumerate(pool.imap_unordered(run_one, recs, chunksize=8)):
            if r is None:
                continue
            if "error" in r:
                errs += 1
                continue
            R.append(r)
            if len(R) % 250 == 0:
                sys.stderr.write(f"[bt2] kept={len(R)} done={i+1}/{len(recs)} err={errs} {time.time()-t0:.0f}s\n")
                sys.stderr.flush()
    with open(OUTDIR + "/policy_backtest_v2_raw.jsonl", "w") as f:
        for r in R:
            f.write(json.dumps(r) + "\n")

    import statistics as st

    def med(xs):
        return round(st.median(xs), 1) if xs else None

    def p90(xs):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 1) if xs else None

    def pc(x, n):
        return f"{x}/{n}={100.0*x/n:.1f}%" if n else "0"

    L = ["=== OBJECTIVE BACKTEST (makespan BASE vs food-age FOODAGE) — production engine ===",
         f"window {a.frm}..{a.to} | eligible_bag>={a.min_bag}={elig} sampled={len(recs)} "
         f"ortools-usable n={len(R)} err={errs} | OR-Tools=200ms sla={SLA} far_km={FAR_KM}",
         f"wall={time.time()-t0:.0f}s", ""]

    # by bag size + overall
    buckets = defaultdict(list)
    for r in R:
        buckets[r["bag"] if r["bag"] <= 5 else 6].append(r)
    buckets["ALL"] = R

    def block(label, rows):
        n = len(rows)
        if not n:
            return [f"── {label}: n=0 ──", ""]
        changed = sum(1 for r in rows if r["base"]["seq"] != r["fa"]["seq"])
        base_th = [r["base"]["thermal"] for r in rows if isinstance(r["base"]["thermal"], (int, float))]
        fa_th = [r["fa"]["thermal"] for r in rows if isinstance(r["fa"]["thermal"], (int, float))]
        base_breach = sum(1 for r in rows if r["base"]["r6_cnt"] > 0)
        fa_breach = sum(1 for r in rows if r["fa"]["r6_cnt"] > 0)
        # on changed bags: thermal delta + span (drive) cost
        d_th_changed, d_span_changed, r6_saved = [], [], 0
        th_imp = th_reg = 0
        for r in rows:
            r6_saved += r["base"]["r6_cnt"] - r["fa"]["r6_cnt"]
            tb, tf = r["base"]["thermal"], r["fa"]["thermal"]
            if isinstance(tb, (int, float)) and isinstance(tf, (int, float)):
                if tb - tf > 0.5:
                    th_imp += 1
                elif tb - tf < -0.5:
                    th_reg += 1
                if r["base"]["seq"] != r["fa"]["seq"]:
                    d_th_changed.append(round(tb - tf, 1))
                    d_span_changed.append(round((r["fa"]["span"] or 0) - (r["base"]["span"] or 0), 1))
        new_reg = sum(1 for r in rows if r["fa"]["sla"] > r["base"]["sla"])
        out = [f"── {label} (n={n}) ──",
               f"  changed-rate (food-age≠makespan seq): {pc(changed,n)}",
               f"  G1 NEW SLA regressions (fa>base): {pc(new_reg,n)}  [GO if ~0]",
               f"  food-age median: BASE={med(base_th)} FA={med(fa_th)} | p90: BASE={p90(base_th)} FA={p90(fa_th)}",
               f"  bags w/ R6 breach(>35): BASE={pc(base_breach,n)} FA={pc(fa_breach,n)} | NET breaches removed={r6_saved}",
               f"  food-age fresher>0.5: {pc(th_imp,n)} regressed<-0.5: {pc(th_reg,n)}"]
        if d_th_changed:
            out.append(f"  Δfood-age on CHANGED: median={med(d_th_changed)} p90={p90(d_th_changed)}min (+=fresher) "
                       f"| drive cost median={med(d_span_changed)} p90={p90(d_span_changed)}min")
        out.append("")
        return out

    L += ["########## OPTION B — FOOD-AGE OBJECTIVE (per bag size) ##########", ""]
    for k in [2, 3, 4, 5, 6, "ALL"]:
        lab = f"bag={k}" if isinstance(k, int) and k <= 5 else ("bag>=6" if k == 6 else "ALL bag>=2")
        L += block(lab, buckets[k])

    # Option C — committed-order delivery lateness (vs direct run) under BASE
    all_late = [x for r in R for x in r["commit_late"]]
    late_pos = [x for x in all_late if x > 0]
    L += ["########## OPTION C — CZASÓWKA DELIVERY LATENESS (committed orders, BASE) ##########",
          f"  committed-order deliveries measured: {len(all_late)}",
          f"  delivered LATE vs direct-run ideal (>0min): {pc(len(late_pos),len(all_late))}",
          f"  lateness median(all)={med(all_late)} p90={p90(all_late)} | median(late only)={med(late_pos)} p90={p90(late_pos)}min",
          f"  late >5min: {pc(sum(1 for x in all_late if x>5),len(all_late))} | >10min: {pc(sum(1 for x in all_late if x>10),len(all_late))}",
          ""]

    # Option D — far-bundle frequency + its food-age penalty
    far = [r for r in R if r["far_bundle"]]
    nfar = [r for r in R if not r["far_bundle"]]
    far_th = [r["base"]["thermal"] for r in far if isinstance(r["base"]["thermal"], (int, float))]
    nfar_th = [r["base"]["thermal"] for r in nfar if isinstance(r["base"]["thermal"], (int, float))]
    far_breach = sum(1 for r in far if r["base"]["r6_cnt"] > 0)
    L += ["########## OPTION D — FAR-BUNDLE (committed deliv >%.1fkm from rest centroid) ##########" % FAR_KM,
          f"  far-bundle decisions: {pc(len(far),len(R))}",
          f"  food-age median: FAR={med(far_th)} vs NON-FAR={med(nfar_th)} | p90 FAR={p90(far_th)} NON-FAR={p90(nfar_th)}",
          f"  R6 breach rate in FAR bundles: {pc(far_breach,len(far))}",
          ""]

    rep = "\n".join(L)
    with open(OUTDIR + "/policy_backtest_v2_result.txt", "w") as f:
        f.write(rep + "\n")
    print(rep)


if __name__ == "__main__":
    main()
