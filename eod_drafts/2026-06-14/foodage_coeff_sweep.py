#!/usr/bin/env python3
"""Sweep coeff food-age (additive) — znajdź punkt gdzie ogon regresji SLA w dużych
workach znika, a zysk (Jakub + thermal) trzyma. OFF liczony RAZ per rekord, ON dla
każdego coeff porównany do TEGO SAMEGO OFF (izoluje efekt coeff od szumu solvera).
Uwaga: OR-Tools 200ms = niedeterministyczny → liczby ±szum, patrz TREND nie wartości.
"""
import json
import sys
from datetime import datetime, timezone, timedelta
import statistics as st
from collections import defaultdict

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)
from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim  # noqa: E402
from dispatch_v2.route_metrics import compute_plan_metrics  # noqa: E402

CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
WINDOW_DAYS, MAX, SLA = 7.0, 1200, 35
COEFFS = [2.0, 3.0, 4.0, 6.0, 8.0]
_W = __import__("zoneinfo").ZoneInfo("Europe/Warsaw")


def _dt(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
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


def _order(p):
    ev = [(t, "P", o) for o, t in (p.pickup_at or {}).items()]
    ev += [(t, "D", o) for o, t in (p.predicted_delivered_at or {}).items()]
    ev.sort(key=lambda e: e[0])
    return [f"{k}:{o}" for _, k, o in ev]


def _jakub_flips(coeff):
    """Czy case Jakuba flipuje na B przy danym coeff (2-gi przystanek = dostawa 480581)."""
    def w(h, m):
        return datetime(2026, 6, 14, h, m, tzinfo=_W).astimezone(timezone.utc)
    o581 = OrderSim('480581', (53.137686, 23.168566), (53.1320984, 23.1915573), None, 'assigned', pickup_ready_at=w(12, 57))
    o568 = OrderSim('480568', (53.126106, 23.162215), (53.1485181, 23.1976805), None, 'assigned', pickup_ready_at=w(13, 14))
    o581.czas_kuriera_warsaw = w(12, 57).isoformat()
    o568.czas_kuriera_warsaw = w(13, 14).isoformat()
    C.OBJ_DELIVERY_FOOD_AGE_COEFF = coeff
    with C.food_age_override(True):
        p = simulate_bag_route_v2((53.137686, 23.168566), [o581], o568, now=w(12, 57), sla_minutes=35)
    ev = _order(p)
    return len(ev) > 1 and ev[1] == "D:480581"


def main():
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
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
            ts = _dt(d.get("ts"))
            if ts is None or ts < cutoff or len(d.get("bag") or []) < 1:
                continue
            recs.append(d)
    if len(recs) > MAX:
        stride = len(recs) / MAX
        recs = [recs[int(i * stride)] for i in range(MAX)]

    # agregaty per coeff
    agg = {c: {"n": 0, "changed": 0, "sla_reg": 0, "sla_imp": 0, "th": [],
               "reg_bigbag": 0} for c in COEFFS}
    for d in recs:
        try:
            cp, now = d.get("courier_pos"), _dt(d.get("now"))
            if not cp or len(cp) != 2 or now is None:
                continue
            bag = [_mk(o) for o in d.get("bag") or []]
            no = _mk(d.get("new_order") or {})
            if no is None or any(b is None for b in bag):
                continue
            kw = dict(now=now, sla_minutes=SLA)
            if d.get("dwell_pickup") is not None:
                kw["dwell_pickup"] = d["dwell_pickup"]
            if d.get("dwell_dropoff") is not None:
                kw["dwell_dropoff"] = d["dwell_dropoff"]
            # OFF raz (R6 only, override wymusza tylko ON)
            p_off = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
            if p_off.strategy != "ortools":
                continue
            off_order = _order(p_off)
            so = p_off.sla_violations or 0
            m_off = compute_plan_metrics(p_off, d.get("dwell_pickup") or 1.0)
            big = len(bag) >= 3
            for c in COEFFS:
                C.OBJ_DELIVERY_FOOD_AGE_COEFF = c
                with C.food_age_override(True):
                    p_on = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
                a = agg[c]
                a["n"] += 1
                if _order(p_on) != off_order:
                    a["changed"] += 1
                    m_on = compute_plan_metrics(p_on, d.get("dwell_pickup") or 1.0)
                    to, tn = m_off.get("max_thermal_age_min"), m_on.get("max_thermal_age_min")
                    if isinstance(to, (int, float)) and isinstance(tn, (int, float)):
                        a["th"].append(to - tn)
                sn = p_on.sla_violations or 0
                if sn > so:
                    a["sla_reg"] += 1
                    if big:
                        a["reg_bigbag"] += 1
                elif sn < so:
                    a["sla_imp"] += 1
        except Exception:
            continue

    jak = {c: _jakub_flips(c) for c in COEFFS}
    print(f"=== SWEEP COEFF food-age (additive) — sample {len(recs)} rek., window {WINDOW_DAYS}d ===")
    print("(OR-Tools 200ms niedeterministyczny → patrz TREND. reg=regresja SLA, imp=poprawa)")
    print(f"{'coeff':>5} {'changed%':>9} {'sla_reg':>8} {'(bag≥3)':>8} {'sla_imp':>8} {'net_sla':>8} {'thermal_mean':>13} {'jakub→B':>8}")
    for c in COEFFS:
        a = agg[c]
        n = a["n"] or 1
        cr = 100.0 * a["changed"] / n
        net = a["sla_imp"] - a["sla_reg"]
        thm = round(st.mean(a["th"]), 2) if a["th"] else None
        print(f"{c:>5} {cr:>8.1f}% {a['sla_reg']:>8} {a['reg_bigbag']:>8} {a['sla_imp']:>8} "
              f"{net:>+8} {str(thm):>13} {'TAK' if jak[c] else 'NIE':>8}")


if __name__ == "__main__":
    main()
