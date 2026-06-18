#!/usr/bin/env python3
"""Multi-policy routing backtest over obj_replay_capture corpus.

Per real decision (bag>=2), compute the route under 3 policies with the
PRODUCTION engine (route_simulator_v2) and score with compute_plan_metrics:

  STICKY  = base_sequence locked (bag dropoffs in pickup-ready order) + food-age OFF
            == today's LIVE PLAN behaviour (plan_manager incremental / V3.19d sticky)
  FREE    = full re-TSP (no base_sequence) + food-age OFF        -> Option A (re-TSP)
  FREE_FA = full re-TSP + ENABLE_OBJ_FOOD_AGE_HARD_SLA ON        -> Option B (food-age, 21.06 sprint)

Comparisons:
  Option A  (re-TSP)            : FREE    vs STICKY
  Option B  (food-age marginal): FREE_FA vs FREE
  Option A+B                    : FREE_FA vs STICKY

Also captures, for committed bag orders (czas_kuriera_warsaw set), planned
delivery time under each policy -> sizes Option C (czasówka delivery window).

Read-only. Nothing flips/sends. Mirrors foodage_phase4_validation conventions.
Usage: policy_backtest.py [--from D] [--to D] [--max N] [--min-bag K] [--workers W]
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from collections import Counter
from multiprocessing import Pool

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)

CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
OUT = SCRIPTS + "/dispatch_v2/eod_drafts/2026-06-18/policy_backtest_result.txt"
SLA = 35

# Engine imported lazily per-worker (heavy module load)
_ENG = {}


def _eng():
    if not _ENG:
        from dispatch_v2 import common as C
        import dispatch_v2.common as CC
        from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim
        from dispatch_v2.route_metrics import compute_plan_metrics
        CC.V326_OR_TOOLS_TIME_LIMIT_MS = 200  # prod limit
        _ENG.update(C=C, CC=CC, sim=simulate_bag_route_v2,
                    OrderSim=OrderSim, metrics=compute_plan_metrics)
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


def _ready_key(o):
    # sort key for sticky base_sequence: pickup_ready_at, else far future
    r = getattr(o, "pickup_ready_at", None)
    return r if r is not None else datetime(2099, 1, 1, tzinfo=timezone.utc)


def _scores(plan, metrics, dwell_p):
    m = metrics(plan, dwell_p or 1.0)
    return {
        "thermal": m.get("max_thermal_age_min"),
        "r6_cnt": m.get("r6_breach_count") or 0,
        "r6_max": m.get("r6_breach_max_min") or 0.0,
        "span": m.get("route_span_min"),
        "sla": plan.sla_violations or 0,
        "seq": list(plan.sequence),
        "strategy": plan.strategy,
        "deliv": {k: v.isoformat() for k, v in (plan.predicted_delivered_at or {}).items()},
    }


def run_one(d):
    try:
        E = _eng()
        OrderSim, sim, metrics, C, CC = (E["OrderSim"], E["sim"], E["metrics"], E["C"], E["CC"])
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

        # committed order ids (czasówka-like): bag orders w/ committed pickup time
        committed = [b.order_id for b in bag if getattr(b, "czas_kuriera_warsaw", None)]

        # base_sequence for sticky = bag dropoffs in pickup-ready order
        sticky_seq = [b.order_id for b in sorted(bag, key=_ready_key)]

        CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = False
        p_free = sim(tuple(cp), bag, no, **kw)
        if p_free.strategy != "ortools":
            return None  # only sequence-optimizable decisions
        p_sticky = sim(tuple(cp), bag, no, base_sequence=sticky_seq, **kw)

        CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = True
        with C.food_age_override(True):
            p_fa = sim(tuple(cp), bag, no, **kw)
        CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = False

        return {
            "ts": (d.get("ts") or "")[:19],
            "bag": len(bag),
            "committed": committed,
            "sticky": _scores(p_sticky, metrics, dp),
            "free": _scores(p_free, metrics, dp),
            "fa": _scores(p_fa, metrics, dp),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2026-06-04T00:00")
    ap.add_argument("--to", dest="to", default="2026-06-18T23:59")
    ap.add_argument("--max", type=int, default=2500)
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
            if ts < a.frm or ts > a.to:
                continue
            if len(d.get("bag") or []) < a.min_bag:
                continue
            recs.append(d)
    total_elig = len(recs)
    if len(recs) > a.max:
        stride = len(recs) / a.max
        recs = [recs[int(i * stride)] for i in range(a.max)]

    sys.stderr.write(f"[bt] eligible={total_elig} sampled={len(recs)} workers={a.workers}\n")
    sys.stderr.flush()

    results = []
    errors = 0
    t0 = time.time()
    with Pool(a.workers) as pool:
        for i, r in enumerate(pool.imap_unordered(run_one, recs, chunksize=8)):
            if r is None:
                continue
            if "error" in r:
                errors += 1
                continue
            results.append(r)
            if len(results) % 200 == 0:
                el = time.time() - t0
                sys.stderr.write(f"[bt] kept={len(results)} done={i+1}/{len(recs)} err={errors} {el:.0f}s\n")
                sys.stderr.flush()

    # persist raw for follow-up (Option C czasówka analysis)
    raw_path = SCRIPTS + "/dispatch_v2/eod_drafts/2026-06-18/policy_backtest_raw.jsonl"
    with open(raw_path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    # ---- aggregate ----
    import statistics as st

    def pct(x, n):
        return f"{x}/{n} = {100.0*x/n:.1f}%" if n else "n=0"

    def med(xs):
        return round(st.median(xs), 2) if xs else None

    def p90(xs):
        if not xs:
            return None
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 2)

    def compare(label, a_key, b_key, lines):
        """a_key = baseline, b_key = treatment. Positive d_* = treatment better."""
        n = len(results)
        changed = thermal_imp = thermal_reg = r6_imp = r6_reg = 0
        d_thermal_changed = []
        d_span_changed = []
        r6_saved = 0
        d_thermal_all = []
        for r in results:
            A, B = r[a_key], r[b_key]
            if A["seq"] != B["seq"]:
                changed += 1
            ta, tb = A["thermal"], B["thermal"]
            if isinstance(ta, (int, float)) and isinstance(tb, (int, float)):
                d = ta - tb  # +ve = treatment fresher
                d_thermal_all.append(d)
                if A["seq"] != B["seq"]:
                    d_thermal_changed.append(d)
                    d_span_changed.append((B["span"] or 0) - (A["span"] or 0))
                if d > 0.5:
                    thermal_imp += 1
                elif d < -0.5:
                    thermal_reg += 1
            dr = A["r6_cnt"] - B["r6_cnt"]
            r6_saved += dr
            if dr > 0:
                r6_imp += 1
            elif dr < 0:
                r6_reg += 1
        lines.append(f"── {label} (baseline={a_key} treatment={b_key}, n={n}) ──")
        lines.append(f"  changed sequence: {pct(changed, n)}")
        lines.append(f"  max-food-age fresher>0.5min: {pct(thermal_imp, n)} | regressed<-0.5: {pct(thermal_reg, n)}")
        if d_thermal_changed:
            lines.append(f"  Δfood-age on CHANGED bags: median={med(d_thermal_changed)} p90={p90(d_thermal_changed)} "
                         f"(+ = treatment fresher) n_changed={len(d_thermal_changed)}")
            lines.append(f"  drive cost on CHANGED bags (treatment-baseline span): median={med(d_span_changed)}min p90={p90(d_span_changed)}min")
        lines.append(f"  R6 breaches: improved_bags={r6_imp} regressed_bags={r6_reg} NET_breaches_removed={r6_saved}")
        if d_thermal_all:
            tot_saved = round(sum(x for x in d_thermal_all if x > 0), 0)
            lines.append(f"  total food-age-minutes saved (sum of positive Δ): {tot_saved}")
        lines.append("")

    L = []
    L.append("=== MULTI-POLICY ROUTING BACKTEST (production engine route_simulator_v2) ===")
    L.append(f"window {a.frm}..{a.to} | eligible_bag>={a.min_bag}={total_elig} sampled={len(recs)} "
             f"ortools-usable n={len(results)} err={errors} | OR-Tools=200ms sla={SLA}")
    L.append(f"wall={time.time()-t0:.0f}s")
    L.append("")
    # baseline pathology
    base_r6 = sum(1 for r in results if r["sticky"]["r6_cnt"] > 0)
    free_r6 = sum(1 for r in results if r["free"]["r6_cnt"] > 0)
    fa_r6 = sum(1 for r in results if r["fa"]["r6_cnt"] > 0)
    L.append("── BASELINE PATHOLOGY (bags with >=1 R6 breach >35min) ──")
    L.append(f"  STICKY(live-plan): {pct(base_r6, len(results))} | FREE(re-TSP): {pct(free_r6, len(results))} | FREE_FA: {pct(fa_r6, len(results))}")
    th_sticky = [r["sticky"]["thermal"] for r in results if isinstance(r["sticky"]["thermal"], (int, float))]
    th_free = [r["free"]["thermal"] for r in results if isinstance(r["free"]["thermal"], (int, float))]
    th_fa = [r["fa"]["thermal"] for r in results if isinstance(r["fa"]["thermal"], (int, float))]
    L.append(f"  median max-food-age: STICKY={med(th_sticky)} FREE={med(th_free)} FREE_FA={med(th_fa)} | "
             f"p90: STICKY={p90(th_sticky)} FREE={p90(th_free)} FREE_FA={p90(th_fa)}")
    L.append("")
    compare("OPTION A — re-TSP (free vs sticky/incremental)", "sticky", "free", L)
    compare("OPTION B — food-age marginal (free_fa vs free)", "free", "fa", L)
    compare("OPTION A+B — re-TSP + food-age (free_fa vs sticky)", "sticky", "fa", L)

    report = "\n".join(L)
    with open(OUT, "w") as f:
        f.write(report + "\n")
    print(report)
    sys.stderr.write(f"[bt] raw -> {raw_path}\n")


if __name__ == "__main__":
    main()
