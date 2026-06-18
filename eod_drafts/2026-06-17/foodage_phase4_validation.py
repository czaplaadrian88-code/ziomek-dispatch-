#!/usr/bin/env python3
"""FAZA 4 — replay-walidacja GO/NO-GO food-age hard-SLA (PHASE1_DESIGN_LOCK §6).

Dla każdej ortools-decyzji w korpusie:
  base    = food-age OFF (= dzisiejszy prod)
  hardsla = food-age ON + ENABLE_OBJ_FOOD_AGE_HARD_SLA ON (HYBRYDA: base→ON+span+warm→fallback)

Bramki DoD:
  G1 (zero regresji): #(sla_hardsla > sla_base) ≈ 0  [twarda gwarancja konstrukcji]
  G2 (zysk zachowany): changed-rate>0 + thermal+ na SLA-neutralnych (food-age wciąż działa)
  G3 (latencja): hardsla = +1 warm-startowany solve; p50/p95 czasu hardsla vs base
  + fallback-rate (hardsla==base = stracony zysk food-age na tym case)

Read-only. Nic nie flipuje/wysyła. Replikuje _mk/filtr drilldownu.
Użycie: foodage_phase4_validation.py [--from D] [--to D] [--max N] [--min-bag K]
"""
import argparse
import json
import sys
import time
import statistics as st
from datetime import datetime, timezone
from collections import Counter

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)
from dispatch_v2 import common as C  # noqa: E402
import dispatch_v2.common as CC  # noqa: E402
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim  # noqa: E402
from dispatch_v2.route_metrics import compute_plan_metrics  # noqa: E402

CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
SLA = 35


def _dt(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except Exception:
        return None


def _mk(d):
    pc, dc = d.get("pickup_coords"), d.get("delivery_coords")
    if not pc or not dc or len(pc) != 2 or len(dc) != 2:
        return None
    o = OrderSim(d.get("order_id"), tuple(pc), tuple(dc),
                 _dt(d.get("picked_up_at")), d.get("status") or "assigned",
                 pickup_ready_at=_dt(d.get("pickup_ready_at")))
    if d.get("czas_kuriera_warsaw"):
        o.czas_kuriera_warsaw = d["czas_kuriera_warsaw"]
    return o


def _p(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2026-06-10T00:00")
    ap.add_argument("--to", dest="to", default="2026-06-17T23:59")
    ap.add_argument("--max", type=int, default=9000)
    ap.add_argument("--min-bag", type=int, default=3)
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

    CC.V326_OR_TOOLS_TIME_LIMIT_MS = 200  # prod limit

    n = 0
    new_reg = 0           # G1: sla_hardsla > sla_base
    new_reg_cases = []
    changed = 0           # hardsla sekwencja != base (food-age zadziałał)
    fallback = 0          # hardsla == base (sla równe i sekwencja równa)
    th_gains = []         # max_thermal base - hardsla (+ = świeższe)
    sla_improved = 0      # hardsla naprawił breach który base miał (sla_hardsla < sla_base)
    lat_base = []
    lat_hard = []
    by_bag_newreg = Counter()
    proc = 0
    for d in recs:
        proc += 1
        if proc % 300 == 0:
            sys.stderr.write(f"[f4] {proc}/{len(recs)} n={n} new_reg={new_reg} changed={changed}\n")
            sys.stderr.flush()
        try:
            cp = d.get("courier_pos")
            now = _dt(d.get("now"))
            if not cp or len(cp) != 2 or now is None:
                continue
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            bag = [_mk(o) for o in d.get("bag") or []]
            no = _mk(d.get("new_order") or {})
            if no is None or any(b is None for b in bag):
                continue
            dp, dd = d.get("dwell_pickup"), d.get("dwell_dropoff")
            kw = dict(now=now, sla_minutes=SLA)
            if dp is not None:
                kw["dwell_pickup"] = dp
            if dd is not None:
                kw["dwell_dropoff"] = dd
            # base (food-age OFF)
            CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = False
            t0 = time.perf_counter()
            p_base = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
            lat_base.append((time.perf_counter() - t0) * 1000.0)
            if p_base.strategy != "ortools":
                continue
            # hardsla (food-age ON + flaga ON)
            CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = True
            t0 = time.perf_counter()
            with C.food_age_override(True):
                p_hard = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
            lat_hard.append((time.perf_counter() - t0) * 1000.0)
            n += 1
            sb = p_base.sla_violations or 0
            sh = p_hard.sla_violations or 0
            if sh > sb:
                new_reg += 1
                by_bag_newreg[len(bag)] += 1
                if len(new_reg_cases) < 30:
                    new_reg_cases.append((d.get("ts", "")[:19], len(bag), sb, sh))
            elif sh < sb:
                sla_improved += 1
            if list(p_hard.sequence) != list(p_base.sequence):
                changed += 1
                mb = compute_plan_metrics(p_base, dp or 1.0)
                mh = compute_plan_metrics(p_hard, dp or 1.0)
                tb, th = mb.get("max_thermal_age_min"), mh.get("max_thermal_age_min")
                if isinstance(tb, (int, float)) and isinstance(th, (int, float)):
                    th_gains.append(round(tb - th, 1))
            else:
                fallback += 1
        except Exception as e:
            sys.stderr.write(f"[skip] {type(e).__name__}: {e}\n")
            continue
    CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = False

    L = []
    L.append("=== FAZA 4: replay-walidacja GO/NO-GO food-age hard-SLA ===")
    L.append(f"okno {a.frm}..{a.to} | eligible={total_elig} re-sim={len(recs)} | prod_ms=200 sla={SLA}")
    L.append(f"ortools-decyzji n={n}")
    L.append("")
    L.append("── G1: ZERO REGRESJI (sla_hardsla > sla_base) ──")
    L.append(f"  nowe regresje: {new_reg}/{n} = {100.0*new_reg/n:.3f}%" if n else "n=0")
    L.append(f"  (referencja: additive @200ms dawał ~0.99%/77 z PHASE0)")
    if new_reg:
        L.append(f"  po bag: {dict(sorted(by_bag_newreg.items()))}")
        for c in new_reg_cases:
            L.append(f"    [{c[0]}] bag={c[1]} sla base={c[2]}→hardsla={c[3]}")
    L.append("")
    L.append("── G2: ZYSK FOOD-AGE ZACHOWANY ──")
    L.append(f"  changed-rate (sekwencja hardsla≠base): {changed}/{n} = {100.0*changed/n:.1f}%" if n else "")
    L.append(f"  fallback (hardsla==base, brak zmiany): {fallback}/{n} = {100.0*fallback/n:.1f}%" if n else "")
    L.append(f"  sla_improved (hardsla naprawił breach base): {sla_improved}")
    if th_gains:
        pos = [x for x in th_gains if x > 0]
        L.append(f"  thermal-trade (base-hardsla) na zmienionych: n={len(th_gains)} "
                 f"median={_p(th_gains,0.5)} p90={_p(th_gains,0.9)} zysk>0 w {len(pos)}/{len(th_gains)}")
    L.append("")
    L.append("── G3: LATENCJA (hardsla = base + warm-startowany ON solve) ──")
    if lat_base and lat_hard:
        L.append(f"  base   p50={_p(lat_base,0.5)}ms p95={_p(lat_base,0.95)}ms")
        L.append(f"  hardsla p50={_p(lat_hard,0.5)}ms p95={_p(lat_hard,0.95)}ms")
        L.append(f"  delta median: +{round(st.median(lat_hard)-st.median(lat_base),1)}ms")
    L.append("")
    verdict = "GO" if (n and new_reg == 0 and changed > 0) else ("REVIEW" if n and new_reg/n < 0.002 else "NO-GO")
    L.append(f"── WERDYKT: {verdict} ──")
    L.append("  G1 PASS gdy new_reg=0 (lub <<additive 0.99%) | G2 PASS gdy changed-rate>0 | G3 = koszt latencji do oceny")

    report = "\n".join(L)
    with open(SCRIPTS + "/dispatch_v2/eod_drafts/2026-06-17/foodage_phase4_result.txt", "w") as f:
        f.write(report + "\n")
    print(report)


if __name__ == "__main__":
    main()
