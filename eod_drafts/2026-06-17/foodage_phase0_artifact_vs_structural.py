#!/usr/bin/env python3
"""FAZA 0 — KLASYFIKACJA regresji SLA food-age: ARTEFAKT BUDŻETU SOLVERA vs STRUKTURALNE.

Odkrycie Fazy 0 (case 17:32:11): przy 200ms ON breachuje SLA 3×, przy 1000ms+
ON=0 breachy i identyczny czas co OFF. Czyli food-age NIE wymusza poświęcenia
SLA — breach był artefaktem niezbieżności OR-Tools w 200ms na dużym worku.

Ten skrypt: dla KAŻDEJ regresji znalezionej przy LIMICIE PROD (200ms) re-solve'uje
ON przy WYSOKIM limicie (--hi-ms, kilka prób, bierze NAJLEPSZY=min sla) i klasyfikuje:
  - ARTEFAKT: przy hi-ms sla_on ≤ sla_off  → regresja znika z budżetem → nie strukturalne
  - STRUKTURALNE: przy hi-ms wciąż sla_on > sla_off → genuine objective tradeoff

NIC nie flipuje/wysyła. Read-only. Replikuje filtr/_mk drilldownu.
Użycie: foodage_phase0_artifact_vs_structural.py [--from D] [--to D] [--max N] [--hi-ms 2000] [--tries 3]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from collections import Counter

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)
from dispatch_v2 import common as C  # noqa: E402
import dispatch_v2.common as CC  # noqa: E402
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim  # noqa: E402

CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
SLA = 35
PROD_MS = 200


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


def _sim(cp, bag, no, kw, food_on):
    if food_on:
        with C.food_age_override(True):
            return simulate_bag_route_v2(tuple(cp), bag, no, **kw)
    return simulate_bag_route_v2(tuple(cp), bag, no, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2026-06-14T00:00")
    ap.add_argument("--to", dest="to", default="2026-06-17T23:59")
    ap.add_argument("--max", type=int, default=4000)
    ap.add_argument("--hi-ms", type=int, default=2000)
    ap.add_argument("--tries", type=int, default=3)
    ap.add_argument("--min-bag", type=int, default=1)
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

    n = 0
    regs = []
    CC.V326_OR_TOOLS_TIME_LIMIT_MS = PROD_MS
    _proc = 0
    for d in recs:
        _proc += 1
        if _proc % 300 == 0:
            sys.stderr.write(f"[scan] {_proc}/{len(recs)} ortools_n={n} regs={len(regs)}\n")
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
            p_off = _sim(cp, bag, no, kw, False)
            if p_off.strategy != "ortools":
                continue
            p_on = _sim(cp, bag, no, kw, True)
            n += 1
            if (p_on.sla_violations or 0) <= (p_off.sla_violations or 0):
                continue
            regs.append((d, cp, now, bag, no, kw,
                         p_off.sla_violations or 0, p_on.sla_violations or 0))
        except Exception:
            continue

    sys.stderr.write(f"[200ms] ortools-decyzji={n} regresje={len(regs)}\n")

    # --- re-solve regresji przy hi-ms (kilka prób, best=min sla) ---
    artifact = 0
    structural = 0
    art_by_bag = Counter()
    struct_by_bag = Counter()
    struct_cases = []
    for (d, cp, now, bag, no, kw, sla_off, sla_on200) in regs:
        best_on = 99
        best_dur = None
        for _ in range(a.tries):
            CC.V326_OR_TOOLS_TIME_LIMIT_MS = a.hi_ms
            p_on_hi = _sim(cp, bag, no, kw, True)
            v = p_on_hi.sla_violations or 0
            if v < best_on:
                best_on = v
                best_dur = p_on_hi.total_duration_min
        bagn = len(bag)
        if best_on <= sla_off:
            artifact += 1
            art_by_bag[bagn] += 1
        else:
            structural += 1
            struct_by_bag[bagn] += 1
            struct_cases.append((d.get("ts", "")[:19], bagn, sla_off, sla_on200, best_on, best_dur))
    CC.V326_OR_TOOLS_TIME_LIMIT_MS = PROD_MS

    R = len(regs)
    print("=== FAZA 0: ARTEFAKT BUDŻETU vs STRUKTURALNE (food-age SLA regresje) ===")
    print(f"okno {a.frm}..{a.to} | eligible={total_elig} re-sim={len(recs)} | prod_ms={PROD_MS} hi_ms={a.hi_ms} tries={a.tries}")
    print(f"ortools-decyzji n={n} | regresje@200ms R={R} ({100.0*R/n:.2f}%)" if n else "n=0")
    if R:
        print()
        print(f"  ARTEFAKT BUDŻETU (znika @ {a.hi_ms}ms): {artifact}/{R} = {100.0*artifact/R:.1f}%")
        print(f"  STRUKTURALNE  (zostaje @ {a.hi_ms}ms): {structural}/{R} = {100.0*structural/R:.1f}%")
        print()
        print(f"  artefakt po bag_size: {dict(sorted(art_by_bag.items()))}")
        print(f"  strukturalne po bag_size: {dict(sorted(struct_by_bag.items()))}")
        if struct_cases:
            print()
            print(f"  --- STRUKTURALNE case'y (ts, bag, sla_off, sla_on@200, sla_on@hi, dur@hi) ---")
            for c in struct_cases[:30]:
                print(f"    [{c[0]}] bag={c[1]} sla {c[2]}→{c[3]}@200 →{c[4]}@hi dur={c[5]}")


if __name__ == "__main__":
    main()
