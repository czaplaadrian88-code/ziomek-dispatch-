#!/usr/bin/env python3
"""Diagnostyka 7 regresji SLA z additive-replay (n=1186). Ten sam deterministyczny
sample (window 7d, max 1200) co review → te same przypadki. Dla każdego on_sla>off_sla
dumpuje: bag, OFF↔ON kolejność przystanków, per-order wiek dostawy (delivered−anchor),
i które zlecenie przekroczyło 35min pod ON a nie pod OFF (co food-age popsuł)."""
import json
import sys
from datetime import datetime, timezone, timedelta

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)
from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim  # noqa: E402

CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
WINDOW_DAYS, MAX, SLA = 7.0, 1200, 35


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


def _anchor(o):
    picked = (getattr(o, "status", None) == "picked_up"
              or getattr(o, "picked_up_at", None) is not None)
    a = getattr(o, "picked_up_at", None) if picked else getattr(o, "pickup_ready_at", None)
    if a is not None and a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    return a, ("picked" if picked else "ready")


def _ages(plan, orders):
    """per-order: wiek dostawy (delivered − anchor) w min."""
    out = {}
    for oid, dl in (plan.predicted_delivered_at or {}).items():
        o = orders.get(oid)
        if o is None:
            continue
        a, _src = _anchor(o)
        if a is None or dl is None:
            continue
        if dl.tzinfo is None:
            dl = dl.replace(tzinfo=timezone.utc)
        out[oid] = round((dl - a).total_seconds() / 60.0, 1)
    return out


def _order(plan):
    ev = [(t, "P", o) for o, t in (plan.pickup_at or {}).items()]
    ev += [(t, "D", o) for o, t in (plan.predicted_delivered_at or {}).items()]
    ev.sort(key=lambda e: e[0])
    return " ".join(f"{k}:{o}" for _, k, o in ev)


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

    found = 0
    for idx, d in enumerate(recs):
        try:
            cp, now = d.get("courier_pos"), _dt(d.get("now"))
            if not cp or len(cp) != 2 or now is None:
                continue
            bag = [_mk(o) for o in d.get("bag") or []]
            no = _mk(d.get("new_order") or {})
            if no is None or any(b is None for b in bag):
                continue
            orders = {o.order_id: o for o in bag + [no]}
            kw = dict(now=now, sla_minutes=SLA)
            if d.get("dwell_pickup") is not None:
                kw["dwell_pickup"] = d["dwell_pickup"]
            if d.get("dwell_dropoff") is not None:
                kw["dwell_dropoff"] = d["dwell_dropoff"]
            p_off = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
            if p_off.strategy != "ortools":
                continue
            with C.food_age_override(True):
                p_on = simulate_bag_route_v2(tuple(cp), bag, no, **kw)
            so, sn = (p_off.sla_violations or 0), (p_on.sla_violations or 0)
            if sn <= so:
                continue
            found += 1
            a_off, a_on = _ages(p_off, orders), _ages(p_on, orders)
            newly = [oid for oid in a_on
                     if a_on.get(oid, 0) > SLA and a_off.get(oid, 0) <= SLA]
            print(f"\n========== REGRESJA #{found} (sample idx {idx}) ==========")
            print(f"  new_order={no.order_id} bag={[o.order_id for o in bag]} (bag_size={len(bag)})")
            print(f"  sla_viol: OFF={so} → ON={sn}")
            print(f"  OFF: {_order(p_off)}")
            print(f"  ON : {_order(p_on)}")
            print(f"  wiek dostawy (min) per-order  [* = >35 SLA]:")
            for oid in sorted(set(a_off) | set(a_on)):
                vo, vn = a_off.get(oid), a_on.get(oid)
                fo = "*" if (vo or 0) > SLA else " "
                fn = "*" if (vn or 0) > SLA else " "
                mark = "  <<< NOWY BREACH" if oid in newly else ""
                print(f"    {oid}: OFF={vo}{fo} ON={vn}{fn}{mark}")
        except Exception:
            continue
    print(f"\n=== ZNALEZIONO {found} regresji ===")


if __name__ == "__main__":
    main()
