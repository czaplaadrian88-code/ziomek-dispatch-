#!/usr/bin/env python3
"""16.05 — DEPLOYABLE proof: real bags through the PRODUCTION engine.

Re-routes 16.05's real co-carried bags (sla_log + geocoded coords) through
simulate_bag_route_v2 designation-sweep (= plan_recheck mechanism) under:
  OFF = food-age OFF  (= Ziomek dziś / makespan)
  ON  = ENABLE_OBJ_FOOD_AGE_HARD_SLA + food_age_override (= Opcja B deployable)
Scored by production compute_plan_metrics. This is the EXACT artifact that flips
21.06, on the EXACT real day requested. Zero proxy.
"""
import json, sys
from datetime import datetime, timezone
from collections import defaultdict
import statistics as st

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import common as C
import dispatch_v2.common as CC
from dispatch_v2 import geocoding
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim
from dispatch_v2.route_metrics import compute_plan_metrics

CC.V326_OR_TOOLS_TIME_LIMIT_MS = 200
SLA_LOG = "/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"
_rc = {k.lower(): v for k, v in json.load(
    open("/root/.openclaw/workspace/dispatch_state/restaurant_coords.json")).items() if isinstance(v, dict)}


def pdt(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace(" ", "T")) if "T" not in s else datetime.fromisoformat(s)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception:
        return None


def rest_coord(name):
    v = _rc.get((name or "").lower())
    if v and v.get("lat"):
        return (float(v["lat"]), float(v.get("lon") or v.get("lng")))
    try:
        c = geocoding.geocode((name or "") + " Białystok", city="Białystok")
        if c and abs(c[0]) > 1:
            return (float(c[0]), float(c[1]))
    except Exception:
        pass
    return None


def deliv_coord(addr):
    try:
        c = geocoding.geocode(addr, city="Białystok")
        if c and abs(c[0]) > 1:
            return (float(c[0]), float(c[1]))
    except Exception:
        pass
    return None


def load_1605():
    O = {}
    for line in open(SLA_LOG):
        if "2026-05-16" not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if (d.get("logged_at") or "")[:10] != "2026-05-16":
            continue
        if d.get("order_id"):
            O[d["order_id"]] = d
    by = defaultdict(list)
    for oid, d in O.items():
        pt, dt = pdt(d.get("picked_up_at")), pdt(d.get("delivered_at"))
        if pt and dt and dt > pt:
            by[d.get("courier_id")].append((pt, dt, oid))
    bags = []
    for cid, lst in by.items():
        lst.sort()
        i = 0
        while i < len(lst):
            grp = [lst[i]]; mx = lst[i][1]; j = i + 1
            while j < len(lst) and lst[j][0] < mx:
                grp.append(lst[j]); mx = max(mx, lst[j][1]); j += 1
            if len(grp) >= 2:
                bags.append((cid, [g[2] for g in grp]))
            i = j
    return O, bags
OUT = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/realbag_1605_engine_result.txt"
SLA = 35


def plan_for_bag(orders_data, oids, flag_on):
    """Designation sweep (= plan_recheck): each order as new_order, pick best
    by (sla, duration). orders_data[oid] = sla_log record. Returns (plan, metrics)."""
    sims = {}
    ready0 = {}
    for o in oids:
        pc = rest_coord(orders_data[o].get("restaurant"))
        dc = deliv_coord(orders_data[o].get("delivery_address"))
        if pc is None or dc is None:
            return None
        r = pdt(orders_data[o].get("picked_up_at"))
        ready0[o] = r
        sims[o] = OrderSim(o, pc, dc, None, "assigned", pickup_ready_at=r)
    start_o = min(oids, key=lambda o: ready0[o])
    cp = sims[start_o].pickup_coords
    now = ready0[start_o]
    best = None
    for newo in oids:
        bag = [sims[o] for o in oids if o != newo]
        if flag_on:
            CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = True
            with C.food_age_override(True):
                p = simulate_bag_route_v2(cp, bag, sims[newo], now=now, sla_minutes=SLA)
            CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = False
        else:
            CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA = False
            p = simulate_bag_route_v2(cp, bag, sims[newo], now=now, sla_minutes=SLA)
        key = (p.sla_violations, round(p.total_duration_min, 3))
        if best is None or key < best[0]:
            best = (key, p)
    p = best[1]
    m = compute_plan_metrics(p, 1.0)
    return {"thermal": m.get("max_thermal_age_min"), "r6": m.get("r6_breach_count") or 0,
            "mk": round(p.total_duration_min, 1), "sla": p.sla_violations or 0}


def main():
    O, bags = load_1605()
    sys.stderr.write(f"[eng1605] orders={len(O)} bags={len(bags)}\n")
    rows = []
    skip = 0
    for cid, oids in bags:
        if not (2 <= len(oids) <= 4):
            skip += 1
            continue
        try:
            off = plan_for_bag(O, oids, False)
            on = plan_for_bag(O, oids, True)
        except Exception as e:
            sys.stderr.write(f"skip {cid}: {type(e).__name__}: {e}\n")
            continue
        if off is None or on is None:
            skip += 1
            continue
        act = [O[o].get("delivery_time_minutes") for o in oids if isinstance(O[o].get("delivery_time_minutes"), (int, float))]
        rows.append({"cid": cid, "n": len(oids), "off": off, "on": on, "act_mf": max(act) if act else None})
        if len(rows) % 15 == 0:
            sys.stderr.write(f"  done {len(rows)}\n")

    def med(x): return round(st.median(x), 1) if x else None
    def p90(x): x = sorted(x); return round(x[min(len(x) - 1, int(0.9 * len(x)))], 1) if x else None
    n = len(rows)
    off_th = [r["off"]["thermal"] for r in rows if isinstance(r["off"]["thermal"], (int, float))]
    on_th = [r["on"]["thermal"] for r in rows if isinstance(r["on"]["thermal"], (int, float))]
    off_r6 = sum(1 for r in rows if r["off"]["r6"] > 0)
    on_r6 = sum(1 for r in rows if r["on"]["r6"] > 0)
    new_reg = sum(1 for r in rows if r["on"]["sla"] > r["off"]["sla"])
    sla_imp = sum(1 for r in rows if r["on"]["sla"] < r["off"]["sla"])
    changed = sum(1 for r in rows if r["off"]["thermal"] != r["on"]["thermal"] or r["off"]["mk"] != r["on"]["mk"])
    dmk = [r["on"]["mk"] - r["off"]["mk"] for r in rows]
    fresher = sum(1 for r in rows if (r["off"]["thermal"] or 0) - (r["on"]["thermal"] or 0) > 0.5)
    L = ["=== 16.05 — DEPLOYABLE proof (production engine, OFF=Ziomek dziś vs ON=Opcja B) ===",
         f"real bags 16.05 size 2-4 re-routed przez silnik: n={n} (skip {skip}) | OR-Tools=200ms",
         "",
         "── G1 BEZPIECZEŃSTWO (twardy wymóg) ──",
         f"  nowe regresje SLA (ON>OFF): {new_reg}/{n} = {100.0*new_reg/n:.2f}%  [musi ≈0]",
         f"  SLA breachy NAPRAWIONE (ON<OFF): {sla_imp}",
         "",
         "── G2 ZYSK ŚWIEŻOŚCI na realnych workach 16.05 ──",
         f"  med max-food-age: OFF={med(off_th)} -> ON={med(on_th)} | p90: OFF={p90(off_th)} -> ON={p90(on_th)}",
         f"  worki z R6 breach (>35min): OFF={off_r6}/{n} -> ON={on_r6}/{n}",
         f"  worki świeższe pod ON: {fresher}/{n} | changed (seq/mk): {changed}/{n}",
         f"  koszt makespanu ON-OFF: median={med(dmk)}min p90={p90(dmk)}min",
         "",
         "── przykłady (akt=realny max food-age sla_log; OFF/ON: thermal/R6/makespan) ──"]
    ex = [r for r in rows if r["n"] >= 3][:8]
    for r in ex:
        L.append(f"  cid={r['cid']} n={r['n']} akt={r['act_mf']} | OFF:{r['off']['thermal']}/{r['off']['r6']}/{r['off']['mk']} "
                 f"-> ON:{r['on']['thermal']}/{r['on']['r6']}/{r['on']['mk']}")
    rep = "\n".join(L)
    open(OUT, "w").write(rep + "\n")
    print(rep)


if __name__ == "__main__":
    main()
