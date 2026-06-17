#!/usr/bin/env python3
"""FAZA 0 (read-only) — root-cause food-age redesign (SPRINT_PLAN_foodage_hard_sla_redesign §4).

Cel Fazy 0:
  1. Na case'ie GENESIS (OFF spokojnie <33 → ON breach z niczego) udowodnić
     mechanizm: SUMA kary food-age po niesionych zleceniach PRZEBIJA karę R6 →
     solver akceptuje breach SLA bo "opłaca się" termicznie.
  2. Potwierdzić kotwicę twardego ograniczenia = pickup_at span (kotwica METRYKI
     _count_sla_violations), różną od kotwicy R6-soft (ready/picked+35).

NIC nie flipuje, NIE wysyła Telegrama, NIE pisze do prod-state. Replikuje logikę
foodage_regression_drilldown.py (ten sam capture, _mk, food_age_override).

Rozkład objektywu: dla SEKWENCJI OFF i SEKWENCJI ON liczy POD KARAMI ON (R6+FA)
sumę kosztów miękkich:
  R6_cost = R6_coeff × Σ_deliveries max(0, off_min − (anchor_min+SLA))
  FA_cost = FA_coeff × Σ_deliveries max(0, off_min − anchor_min)
gdzie off_min=(delivered−now)[min], anchor_min=(anchor−now)[min],
anchor = picked_up_at (odebrane) | pickup_ready_at (pending/new) — DOKŁADNIE jak
route_simulator buduje delivery_soft_deadlines/food_age (l.1041-1088).

Solver minimalizuje (drive+span) + R6_cost + FA_cost. Jeśli ON-seq ma niższy
łączny koszt miękki MIMO wyższego R6 (=breach), to FA jest dźwignią która
przeważyła → potwierdzony mechanizm.
"""
import argparse
import json
import sys
from datetime import datetime, timezone, timedelta

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)
from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim  # noqa: E402

CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
SLA = 35
R6_COEFF = float(getattr(C, "OBJ_R6_DEADLINE_PENALTY_COEFF", 100.0))
FA_COEFF = float(getattr(C, "OBJ_DELIVERY_FOOD_AGE_COEFF", 3.0))


def _dt(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except Exception:
        return None


def _utc(d):
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


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


def _anchor_min(o, now):
    """Kotwica kary R6/food-age jak w route_simulator l.1041-1044: picked→picked_up_at
    else pickup_ready_at. Zwraca (anchor−now) w min lub None gdy brak kotwicy."""
    picked = (getattr(o, "status", "assigned") == "picked_up"
              or getattr(o, "picked_up_at", None) is not None)
    anc = (getattr(o, "picked_up_at", None) if picked
           else getattr(o, "pickup_ready_at", None))
    anc = _utc(anc)
    if anc is None:
        return None
    return (anc - now).total_seconds() / 60.0


def _decompose(plan, bag, new_order, now):
    """Pod karami ON: R6_cost, FA_cost (coeff×Σ overshoot[min]) + lista breachy R6.
    Per-order: kotwica METRYKI (pickup_at|picked_up_at→delivery, span) vs kotwica
    KARY (ready|picked→delivery). Tu dokładnie widać rozjazd §6."""
    da = plan.predicted_delivered_at or {}
    pa = plan.pickup_at or {}
    r6_over = 0.0
    fa_over = 0.0
    breaches = []
    per_order = []
    for o in list(bag) + [new_order]:
        pred = _utc(da.get(o.order_id))
        if pred is None:
            continue
        am = _anchor_min(o, now)          # kotwica KARY (ready|picked)
        off_min = (pred - now).total_seconds() / 60.0
        # kotwica METRYKI: pickup_at(TSP) | picked_up_at | now
        if o.order_id in pa:
            mpu = _utc(pa[o.order_id]); src = "tsp_pickup"
        elif _utc(getattr(o, "picked_up_at", None)) is not None:
            mpu = _utc(o.picked_up_at); src = "picked_up_at"
        else:
            mpu = now; src = "now"
        metric_span = (pred - mpu).total_seconds() / 60.0
        ready_span = (off_min - am) if am is not None else None
        per_order.append({
            "oid": o.order_id, "anchor_src": src,
            "metric_span": round(metric_span, 1),
            "ready_span": (round(ready_span, 1) if ready_span is not None else None),
            "metric_breach": metric_span > SLA,
        })
        if am is None:
            continue
        o_r6 = max(0.0, off_min - (am + SLA))
        o_fa = max(0.0, off_min - am)
        r6_over += o_r6
        fa_over += o_fa
        if o_r6 > 0:
            breaches.append((o.order_id, round(off_min - am, 1), round(o_r6, 1)))
    return {
        "r6_cost": round(R6_COEFF * r6_over, 1),
        "fa_cost": round(FA_COEFF * fa_over, 1),
        "breaches": breaches,
        "per_order": per_order,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2026-06-14T17:20")
    ap.add_argument("--to", dest="to", default="2026-06-14T17:45")
    ap.add_argument("--want", type=int, default=3, help="ile genesis case'ów wypisać")
    ap.add_argument("--min-bag", type=int, default=2)
    a = ap.parse_args()

    lo, hi = a.frm, a.to
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
            ts = d.get("ts") or ""
            if ts[:16] < lo or ts[:16] > hi:
                continue
            if len(d.get("bag") or []) < a.min_bag:
                continue
            recs.append(d)

    print(f"=== FAZA 0 root-cause food-age (read-only) ===")
    print(f"okno: {lo}..{hi} | R6_coeff={R6_COEFF} FA_coeff={FA_COEFF} SLA={SLA}")
    print(f"rekordy w oknie (bag≥{a.min_bag}): {len(recs)}")
    print()

    found = 0
    scanned = 0
    for d in recs:
        if found >= a.want:
            break
        try:
            cp = d.get("courier_pos")
            now = _utc(_dt(d.get("now")))
            if not cp or len(cp) != 2 or now is None:
                continue
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
            p_off = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
            if p_off.strategy != "ortools":
                continue
            with C.food_age_override(True):
                p_on = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
            scanned += 1
            sla_off = p_off.sla_violations or 0
            sla_on = p_on.sla_violations or 0
            if sla_on <= sla_off:
                continue
            # genesis? co najmniej 1 zlecenie breachujące pod ON które pod OFF
            # było spokojnie <33 min (kotwica metryki). Użyj plan delivered/pickup.
            doff = _decompose(p_off, bag, no, now)
            don = _decompose(p_on, bag, no, now)

            print(f"┌─ CASE [{d.get('ts')[:19]}] bag={len(bag)} new_oid={no.order_id}")
            print(f"│  SLA breaches: OFF={sla_off} → ON={sla_on}  (regresja +{sla_on-sla_off})")
            print(f"│  SEKWENCJA OFF: koszt miękki R6={doff['r6_cost']} + FA={doff['fa_cost']} "
                  f"= {round(doff['r6_cost']+doff['fa_cost'],1)}  (R6_breach={doff['breaches']})")
            print(f"│  SEKWENCJA ON : koszt miękki R6={don['r6_cost']} + FA={don['fa_cost']} "
                  f"= {round(don['r6_cost']+don['fa_cost'],1)}  (R6_breach={don['breaches']})")
            d_r6 = round(don['r6_cost'] - doff['r6_cost'], 1)
            d_fa = round(don['fa_cost'] - doff['fa_cost'], 1)
            d_tot = round(d_r6 + d_fa, 1)
            print(f"│  Δ(ON−OFF): R6={'+' if d_r6>=0 else ''}{d_r6}  FA={'+' if d_fa>=0 else ''}{d_fa}  "
                  f"miękki_total={'+' if d_tot>=0 else ''}{d_tot}")
            print(f"│  total_dur(drive+span proxy): OFF={p_off.total_duration_min} ON={p_on.total_duration_min} "
                  f"(Δ={round(p_on.total_duration_min-p_off.total_duration_min,1)})")
            # interpretacja mechanizmu
            if d_fa < 0 and d_r6 > 0:
                drive_delta = p_on.total_duration_min - p_off.total_duration_min
                tipped = (abs(d_fa) + max(0.0, -drive_delta)) >= d_r6
                print(f"│  ⇒ ON oszczędza FA={d_fa} (świeżość) kosztem R6=+{d_r6} (breach). "
                      f"FA-savings{' +drive' if drive_delta<0 else ''} {'PRZEBIJA' if tipped else 'NIE przebija'} R6.")
            print(f"│  PER-ORDER kotwica METRYKI(pickup→del) vs KARY(ready→del) [min]:")
            poff = {x["oid"]: x for x in doff["per_order"]}
            for x in don["per_order"]:
                o = poff.get(x["oid"], {})
                mb_on = "‼" if x["metric_breach"] else " "
                print(f"│     {x['oid']} [{x['anchor_src']:>12}]  "
                      f"OFF metric={o.get('metric_span')!s:>5}/ready={o.get('ready_span')!s:>5}  "
                      f"ON metric={x['metric_span']!s:>5}{mb_on}/ready={x['ready_span']!s:>5}")
            print(f"└─ stop_off={p_off.sequence}  stop_on={p_on.sequence}")
            print()
            found += 1
        except Exception as e:
            sys.stderr.write(f"[skip] {type(e).__name__}: {e}\n")
            continue

    print(f"--- przeskanowano ortools-decyzji: {scanned}, znaleziono genesis: {found} ---")


if __name__ == "__main__":
    main()
