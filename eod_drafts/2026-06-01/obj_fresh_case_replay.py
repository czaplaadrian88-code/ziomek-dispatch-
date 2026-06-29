#!/usr/bin/env python3
"""Per-case route replay dla cytowanych przez Adriana zleceń — pokazuje realną
sekwencję (odbiory/dostawy w kolejności czasowej) pod każdym wariantem objective.
Offline, read-only. Użycie: python obj_fresh_case_replay.py 477706 477632 ..."""
import sys, json
from datetime import datetime, timezone
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import common as C
from dispatch_v2.route_simulator_v2 import OrderSim, simulate_bag_route_v2

CAPTURE = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
C.ENABLE_OBJ_SPAN_COST = True; C.OBJ_SPAN_COST_COEFF = 1.0

VARIANTS = {
    "V0_prod_c20": dict(fresh=True, coeff=20, r6=False, r6c=100),
    "V1_off":      dict(fresh=False, coeff=0, r6=False, r6c=100),
    "V4_sym_c20":  dict(fresh=True, coeff=20, r6=True, r6c=100),
    "V6_r6only":   dict(fresh=False, coeff=0, r6=True, r6c=100),
}


def apply(v):
    C.ENABLE_OBJ_PICKUP_FRESHNESS = v["fresh"]; C.OBJ_PICKUP_FRESHNESS_PENALTY_COEFF = float(v["coeff"])
    C.ENABLE_OBJ_R6_SOFT_DEADLINE = v["r6"]; C.OBJ_R6_DEADLINE_PENALTY_COEFF = float(v["r6c"])


def _dt(s):
    if not s: return None
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _sim(d):
    s = OrderSim(order_id=d.get("order_id"), pickup_coords=tuple(d.get("pickup_coords") or (0, 0)),
                 delivery_coords=tuple(d.get("delivery_coords") or (0, 0)),
                 picked_up_at=_dt(d.get("picked_up_at")), status=d.get("status") or "assigned",
                 pickup_ready_at=_dt(d.get("pickup_ready_at")))
    s.czas_kuriera_warsaw = d.get("czas_kuriera_warsaw"); return s


def hhmm(dt): return dt.astimezone(timezone.utc).strftime("%H:%M") if dt else "--:--"


def shadow_meta(oid):
    with open(SHADOW) as f:
        for line in f:
            if f'"order_id": "{oid}"' in line or f'"order_id": {oid}' in line:
                d = json.loads(line)
                if str(d.get("order_id")) == str(oid):
                    return d
    return {}


def find_rec(oid, seq):
    bag_set = frozenset(str(x) for x in seq if str(x) != str(oid))
    best = None; bestdt = None
    with open(CAPTURE) as f:
        for line in f:
            if f'"{oid}"' not in line: continue
            try: r = json.loads(line)
            except Exception: continue
            no = (r.get("new_order") or {})
            if str(no.get("order_id")) != str(oid): continue
            bset = frozenset(str((o or {}).get("order_id")) for o in (r.get("bag") or []))
            if bset != bag_set: continue
            best = r  # ostatni pasujący
    return best


def replay(oid):
    meta = shadow_meta(oid)
    b = meta.get("best") or {}; pl = b.get("plan") or {}
    seq = [str(x) for x in (pl.get("sequence") or [])]
    rec = find_rec(oid, seq)
    print("\n" + "#" * 78)
    print(f"# oid={oid}  {meta.get('restaurant')} → {meta.get('delivery_address')}  "
          f"kurier={b.get('name')} bag_before={b.get('bag_size_before')}")
    if not rec:
        print("  brak rekordu capture (bag-set mismatch)"); return
    new = _sim(rec["new_order"]); bag = [_sim(o) for o in rec.get("bag", [])]
    cpos = tuple(rec.get("courier_pos") or ()); now = _dt(rec.get("now"))
    ready = new.pickup_ready_at
    print(f"  ready(new)={hhmm(ready)}  now={hhmm(now)}")
    for name, v in VARIANTS.items():
        apply(v)
        plan = simulate_bag_route_v2(cpos, bag, new, now=now,
                                     dwell_pickup=rec.get("dwell_pickup"), dwell_dropoff=rec.get("dwell_dropoff"))
        pa = getattr(plan, "pickup_at", {}) or {}; dv = getattr(plan, "predicted_delivered_at", {}) or {}
        # zbuduj listę zdarzeń (odbiór/dostawa) i sortuj po czasie
        ev = []
        for k, t in pa.items(): ev.append((_dt(t), f"odb {k}{'  <<NOWY' if str(k)==str(oid) else ''}"))
        for k, t in dv.items(): ev.append((_dt(t), f"DOST {k}{'  <<NOWY' if str(k)==str(oid) else ''}"))
        ev = [e for e in ev if e[0]]; ev.sort()
        pu_new = _dt(pa.get(str(oid))); stale = (pu_new - ready).total_seconds()/60 if (pu_new and ready) else None
        chain = "  ".join(f"{hhmm(t)} {lab}" for t, lab in ev)
        print(f"  [{name:11s}] stale={'' if stale is None else round(stale,1):>5}m | {chain}")


if __name__ == "__main__":
    for oid in sys.argv[1:]:
        replay(oid)
